"""The runner-side client against the real gate, over a fake ssh that drops connections."""

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import PY, STUBS, TOOLS, SandboxTestCase, cleanup_templates, make_artifact  # noqa: E402

sys.path.insert(0, str(TOOLS))
import rastisi_common as C  # noqa: E402


def tearDownModule():
    cleanup_templates()


class ClientTests(SandboxTestCase):
    def setUp(self):
        super().setUp()
        self.faults = self.sbx.root / "faults.json"
        self.client_env = dict(self.sbx.env, GATE=str(TOOLS / "rastisi_gate.py"), RASTISI_RETRY_DELAY="0.2",
                               RASTISI_SSH="%s %s %s" % (PY, STUBS / "fake_ssh.py", self.faults))

    def set_faults(self, **f):
        self.faults.write_text(json.dumps(f))

    def client(self, *args, timeout=180):
        proc = subprocess.run([PY, str(TOOLS / "remote_client.py")] + [str(a) for a in args], capture_output=True, text=True, env=self.client_env, timeout=timeout)
        return proc

    def stage(self, **kw):
        art, sha, digest = make_artifact(self.sbx.root, **kw)
        self.sbx._seed_venv()
        proc = self.client("stage", "--sha", sha, "--sha256", digest, "--artifact", art)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout.strip().splitlines()[-1], sha

    def test_stage_activate_follow_end_to_end(self):
        rid, sha = self.stage()
        proc = self.client("activate", "--release", rid, "--sha", sha, "--from-sha", self.legacy_sha)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("RESULT: OK", proc.stdout)
        self.assertIn("DONE", proc.stdout)
        self.assertServing(rid)

    def test_checkpoint_survives_truncated_chunks_and_a_replacement_runner(self):
        from release_upload import CHUNK_SIZE

        content = os.urandom(CHUNK_SIZE * 3)
        artifact, sha, digest = make_artifact(self.sbx.root, extra_files={"payload": content.hex()})
        self.sbx._seed_venv()
        data = artifact.read_bytes()
        identity = f"{sha} {digest} {len(data)}"

        def gate(command, chunk=None):
            return subprocess.run([PY, str(STUBS / "fake_ssh.py"), str(self.faults), command],
                                  input=chunk, capture_output=True, env=self.client_env)

        self.assertGreater(len(data), 2 * CHUNK_SIZE)
        for offset in (0, CHUNK_SIZE):
            chunk = data[offset:offset + CHUNK_SIZE]
            command = f"upload-chunk {identity} {offset} {hashlib.sha256(chunk).hexdigest()}"
            self.assertEqual(gate(command, chunk[:len(chunk) // 2]).returncode, C.EXIT_POLICY)
            self.assertEqual(gate(command, b"x" * len(chunk)).returncode, C.EXIT_POLICY)
            self.assertEqual(json.loads(gate("upload-offset " + identity).stdout)["offset"], offset)
            self.assertEqual(gate("stage-upload " + identity).returncode, C.EXIT_POLICY)
            self.assertEqual(gate(command, chunk).returncode, 0)
            self.assertEqual(gate(command, chunk).returncode, 0)

        self.set_faults(lose_response_for="stage-upload")
        staged = self.client("stage", "--sha", sha, "--sha256", digest, "--artifact", artifact)
        self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
        self.assertIn(f"artifact checkpoint: {2 * CHUNK_SIZE}/", staged.stderr)
        repeat = self.client("stage", "--sha", sha, "--sha256", digest, "--artifact", artifact)
        self.assertEqual(repeat.returncode, 0, repeat.stdout + repeat.stderr)
        self.assertEqual(staged.stdout, repeat.stdout)
        self.assertEqual(sum(h["event"] == "staged" for h in self.sbx.history()), 1)

    def test_whole_file_hash_and_competing_artifact(self):
        data = b"complete chunk with wrong artifact hash"
        sha = "a" * 40
        identity = f"{sha} {'b' * 64} {len(data)}"
        def gate(command, payload=None):
            return subprocess.run([PY, str(STUBS / "fake_ssh.py"), str(self.faults), command],
                                  input=payload, capture_output=True, env=self.client_env)

        self.assertEqual(gate("upload-offset " + identity).returncode, 0)
        other = f"{'c' * 40} {'d' * 64} {len(data)}"
        self.assertEqual(gate("upload-offset " + other).returncode, C.EXIT_POLICY)
        chunk_hash = hashlib.sha256(data).hexdigest()
        self.assertEqual(gate(f"upload-chunk {identity} 0 {chunk_hash}", data).returncode, 0)
        rejected = gate("stage-upload " + identity)
        self.assertEqual(rejected.returncode, C.EXIT_POLICY)
        self.assertIn(b"whole-artifact checksum mismatch", rejected.stderr)
        self.assertTrue(self.current().startswith("legacy-"))

    def test_exit_codes_and_messages_follow_the_server(self):
        rid, sha = self.stage(broken=True)
        proc = self.client("activate", "--release", rid, "--sha", sha, "--from-sha", self.legacy_sha)
        self.assertEqual(proc.returncode, C.EXIT_FAILED_SAFE)
        self.assertIn("ROLLED BACK", proc.stdout)
        other, other_sha = self.stage(migrations=["core.0002_x"], level="ADDITIVE")
        policy = self.client("activate", "--release", other, "--sha", other_sha, "--from-sha", self.legacy_sha)
        self.assertEqual(policy.returncode, C.EXIT_POLICY)
        self.assertIn("REFUSED BY POLICY", policy.stdout)

    def test_connection_refusals_are_retried(self):
        self.set_faults(refuse=3)
        proc = self.client("status")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("connection problem", proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["current"], self.current())

    def test_a_lost_response_to_activate_does_not_start_a_second_deployment(self):
        rid, sha = self.stage()
        self.set_faults(lose_response_for="activate")
        run = "19700101T000000Z-" + "a" * 32
        args = ("activate", "--release", rid, "--sha", sha, "--from-sha", self.legacy_sha, "--run", run)
        proc = self.client(*args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.client(*args).returncode, 0)
        self.assertEqual(self.client(*args, "--dry-run").returncode, C.EXIT_POLICY)
        self.assertEqual(len([h for h in self.sbx.history() if h.get("event") == "activate"]), 1, "exactly one deployment")
        runs = [p for p in (self.sbx.base / "state" / "runs").iterdir() if p.is_dir()]
        self.assertEqual(len(runs), 2)

    def test_a_lost_response_to_rollback_does_not_roll_back_twice(self):
        rid, sha = self.stage()
        self.assertEqual(self.client("activate", "--release", rid, "--sha", sha).returncode, 0)
        self.set_faults(lose_response_for="rollback")
        proc = self.client("rollback", "--to", "previous")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(self.current().startswith("legacy-"), "one rollback, not two (a second one would flip back to the new release)")
        self.assertEqual(len([h for h in self.sbx.history() if h.get("event") == "rollback"]), 1)

    def test_losing_the_connection_while_following_is_survived(self):
        rid, sha = self.stage()
        self.sbx.set_control({"smoke_delay": 2})
        self.set_faults(lose_response_for="follow")
        proc = self.client("activate", "--release", rid, "--sha", sha)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout.count("DONE"), 1, "the log must not be duplicated or lost after a reconnect")

    def test_giving_up_reports_lost_contact_not_failure(self):
        rid, sha = self.stage()
        self.sbx.set_control({"smoke_delay": 4})
        env = dict(self.client_env)
        code = subprocess.run([PY, "-c", """
import sys; sys.path.insert(0, %r)
import remote_client as R, time, os
os.environ['RASTISI_RETRY_DELAY'] = '0.05'
R.call = lambda *a, **k: (255, '', 'down')
print(R.follow('20261112T000000Z-aaaaaaaa', give_up_after=0.3))
""" % str(TOOLS)], capture_output=True, text=True, env=env).stdout.strip()
        self.assertEqual(code, str(10))

    def test_dry_run_flag_reaches_the_server(self):
        rid, sha = self.stage()
        proc = self.client("activate", "--release", rid, "--sha", sha, "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(self.current().startswith("legacy-"))

    def test_without_ssh_configuration_the_client_refuses(self):
        env = {k: v for k, v in self.client_env.items() if k != "RASTISI_SSH"}
        proc = subprocess.run([PY, str(TOOLS / "remote_client.py"), "status"], capture_output=True, text=True, env=env)
        self.assertNotEqual(proc.returncode, 0)

    def current(self):
        return self.sbx.status()["current"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
