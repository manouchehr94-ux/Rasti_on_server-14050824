#!/usr/bin/env python3
"""SSH forced command for the deploy user (standard library only). One fixed list of verbs, strictly validated. No shell, no
arbitrary commands, no file paths from the caller.

authorized_keys (see deploy/release/examples/authorized_keys.example):
    restrict,command="/usr/bin/python3 /usr/local/lib/rastisi/rastisi_gate.py" ssh-ed25519 AAAA... github-actions-deploy

Verbs (the original command line arrives in SSH_ORIGINAL_COMMAND):
    version
    status
    stage    <sha40> <sha256>                 artifact (tar.gz) is read from STDIN, verified, unpacked, venv built
    upload-offset <sha40> <sha256> <size>
    upload-chunk  <sha40> <sha256> <size> <offset> <chunk_sha256>
    stage-upload  <sha40> <sha256> <size>
    activate <release_id> <sha40> <from_sha40|-> [allow-migrations] [destructive-ack=<phrase>] [dry-run] [run=<run_id>]
    rollback [<release_id>|previous] [unsafe-ack=<phrase>] [run=<run_id>]
    follow   <run_id> <offset>                prints new log bytes, then a STATE line (reconnectable at any time)
    result   <run_id>

``run=<run_id>`` is the caller's idempotency key: when the connection drops after the server started a run but before the caller read
"RUN <id>", the caller repeats the same request with the same id and the server answers with the existing run instead of starting a second
one (a second ``rollback`` would flip the system back again).

``activate`` and ``rollback`` run DETACHED (own session, supervised): losing the SSH connection can neither interrupt nor
duplicate a deployment. The caller reconnects and keeps calling ``follow`` until STATE is 'done'.
"""

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rastisi_common as C  # noqa: E402
from release_upload import CHUNK_SIZE, Upload  # noqa: E402
from rastisi_release import Lock  # noqa: E402

HERE = Path(__file__).resolve().parent
RELEASE_TOOL = HERE / "rastisi_release.py"
TOOL_VERSION_FILE = HERE / "TOOLING_VERSION"
MAX_FOLLOW_BYTES = 256 * 1024
PHRASE_RE = re.compile(r"^[A-Za-z0-9-]{10,120}$")


def out(line=""):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def fail(code, message):
    sys.stderr.write(C.redact(message) + "\n")
    return code


def tool_version():
    try:
        return TOOL_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def run_dir(cfg, run_id):
    if not C.RUN_ID_RE.fullmatch(run_id or ""):
        raise ValueError("invalid run id")
    return cfg.state / "runs" / run_id


def release_cmd(*args):
    return [sys.executable, str(RELEASE_TOOL)] + list(args)


def spawn_detached(cfg, run_id, argv):
    """Start ``argv`` under a supervisor in its own session. Returns immediately. The supervisor records the exit code even if the
    tool is killed, so a follower can always tell 'running' from 'done' from 'died'."""
    rd = cfg.state / "runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    with (rd / "log").open("ab", buffering=0) as log:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--supervise", run_id] + argv,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, start_new_session=True,
            env=dict(os.environ),
        )
    C.atomic_write_text(rd / "pid", "%d\n" % proc.pid)
    return proc.pid


def supervise(run_id, argv):
    cfg = C.Config()
    rd = run_dir(cfg, run_id)
    proc = subprocess.Popen(argv)
    C.atomic_write_text(rd / "child_pid", "%d\n" % proc.pid)
    code = proc.wait()
    marker = rd / "exit_code"
    if not marker.exists():
        C.atomic_write_text(marker, "%d\n" % (code if code >= 0 else 128 - code))
    return 0


def pid_alive(pid):
    try:
        # kill(pid, 0) considers zombies alive. A dead supervisor must not leave
        # followers waiting forever for an exit record it cannot write.
        stat = Path('/proc/%d/stat' % pid).read_text()
        if stat.rsplit(')', 1)[1].strip().split()[0] == 'Z':
            return False
        os.kill(pid, 0)
    except (ProcessLookupError, FileNotFoundError):
        return False
    except PermissionError:
        return True
    return True


def v_stage(cfg, argv):
    if len(argv) != 2 or not C.SHA_RE.match(argv[0]) or not C.SHA256_RE.match(argv[1]):
        return fail(C.EXIT_POLICY, "usage: stage <sha40> <sha256>")
    spool = Upload(cfg)
    if not spool.lock.acquire():
        return fail(C.EXIT_BUSY, "another upload command is in progress")
    try:
        return legacy_stage(cfg, argv)
    finally:
        spool.lock.release()


def legacy_stage(cfg, argv):
    target = cfg.staging / ("incoming-%s-%d.tar.gz" % (C.utc_stamp(), os.getpid()))
    limit = cfg.int("max_artifact_mb", 400) * 1024 * 1024
    size = 0
    h = hashlib.sha256()
    try:
        with open(str(target), "xb") as fh:
            while True:
                block = sys.stdin.buffer.read(1 << 20)
                if not block:
                    break
                size += len(block)
                if size > limit:
                    return fail(C.EXIT_POLICY, "artifact exceeds %d MB" % (limit // (1024 * 1024)))
                h.update(block)
                fh.write(block)
        if h.hexdigest() != argv[1]:
            return fail(C.EXIT_POLICY, "artifact checksum mismatch: received %s" % h.hexdigest())
        actor = os.environ.get("RASTISI_ACTOR", "")
        proc = subprocess.run(release_cmd("stage", "--artifact", str(target), "--sha", argv[0], "--sha256", argv[1], "--actor", actor))
        return proc.returncode
    finally:
        try:
            target.unlink()
        except FileNotFoundError:
            pass


def staging_busy(cfg, run_id):
    if not run_id:
        return False
    rd = run_dir(cfg, run_id)
    if (rd / "exit_code").exists():
        return False
    # If a launcher died before recording a PID, require operator inspection.
    for name in ("pid", "child_pid"):
        path = rd / name
        if path.exists() and pid_alive(int(path.read_text(encoding="ascii"))):
            return True
    return not (rd / "pid").exists()


def upload_request(cfg, argv, action):
    expected = 5 if action == "chunk" else 3
    if len(argv) != expected:
        raise ValueError("invalid upload request arguments")
    sha, digest, size_arg = argv[:3]
    if not C.SHA_RE.fullmatch(sha) or not C.SHA256_RE.fullmatch(digest):
        raise ValueError("upload requires a full commit SHA and SHA256")
    if not re.fullmatch(r"[0-9]{1,12}", size_arg):
        raise ValueError("invalid artifact size")
    size = int(size_arg)
    if not 0 < size <= cfg.int("max_artifact_mb", 400) * 1024 * 1024:
        raise ValueError("artifact exceeds configured size bounds")
    if action == "chunk":
        offset_arg, chunk_hash = argv[3:]
        if not re.fullmatch(r"[0-9]{1,12}", offset_arg) or not C.SHA256_RE.fullmatch(chunk_hash):
            raise ValueError("invalid chunk offset or checksum")
        offset = int(offset_arg)
        if offset >= size or offset % CHUNK_SIZE:
            raise ValueError("invalid chunk boundary")
        length = min(CHUNK_SIZE, size - offset)
        data = sys.stdin.buffer.read(length + 1)
        if len(data) != length:
            return fail(C.EXIT_POLICY, "incomplete or oversized chunk; checkpoint unchanged")
        if hashlib.sha256(data).hexdigest() != chunk_hash:
            return fail(C.EXIT_POLICY, "chunk checksum mismatch; checkpoint unchanged")
    spool = Upload(cfg)
    if not spool.lock.acquire():
        return fail(C.EXIT_BUSY, "another upload command is in progress")
    try:
        state = spool.session(sha, digest, size, lambda key: staging_busy(cfg, key))
        if action == "stage":
            if state.get("run") and (run_dir(cfg, state["run"]) / "exit_code").exists():
                exit_code = (run_dir(cfg, state["run"]) / "exit_code").read_text().strip()
                if exit_code == str(C.EXIT_BUSY):
                    del state["run"]
            if "run" not in state:
                spool.finish(state)
                state["run"] = "%s-%s" % (C.utc_stamp(), os.urandom(4).hex())
                spool.save(state)
            actor = os.environ.get("RASTISI_ACTOR", "")
            return start_or_resume(cfg, state["run"], release_cmd(
                "stage", "--artifact", str(spool.ready), "--sha", sha, "--sha256", digest, "--actor", actor,
            ))
        if action == "chunk":
            spool.append(state, offset, data)
        out(json.dumps({"offset": state["offset"], "chunk_size": CHUNK_SIZE}))
        return 0
    finally:
        spool.lock.release()


def v_upload_offset(cfg, argv):
    return upload_request(cfg, argv, "offset")


def v_upload_chunk(cfg, argv):
    return upload_request(cfg, argv, "chunk")


def v_stage_upload(cfg, argv):
    return upload_request(cfg, argv, "stage")


def start_or_resume(cfg, given_run_id, cmd):
    """Serialize launch and bind each idempotency key to its original command."""
    run_id = given_run_id or "%s-%s" % (C.utc_stamp(), os.urandom(4).hex())
    rd = run_dir(cfg, run_id)
    rd.mkdir(parents=True, exist_ok=True)
    lock = Lock(rd / "launch.lock")
    if not lock.acquire():
        return fail(C.EXIT_BUSY, "run is being launched; retry the same id")
    try:
        fingerprint = hashlib.sha256(json.dumps(cmd).encode()).hexdigest()
        request = rd / "request"
        if request.exists():
            if request.read_text(encoding="ascii").strip() != fingerprint:
                return fail(C.EXIT_POLICY, "run id belongs to a different request")
        else:
            if any((rd / marker).exists() for marker in ("pid", "child_pid", "exit_code")):
                return fail(C.EXIT_POLICY, "existing run predates request binding; inspect it with follow")
            C.atomic_write_text(request, fingerprint + "\n")
            spawn_detached(cfg, run_id, cmd + ["--run-id", run_id])
        out("RUN %s" % run_id)
        return 0
    finally:
        lock.release()


def split_run_flag(argv):
    given = ""
    rest = []
    for a in argv:
        if a.startswith("run="):
            given = a.split("=", 1)[1]
            if not C.RUN_ID_RE.fullmatch(given):
                raise ValueError("invalid run id")
        else:
            rest.append(a)
    return given, rest


def v_activate(cfg, argv):
    given, argv = split_run_flag(argv)
    if len(argv) < 3 or not C.RELEASE_ID_RE.match(argv[0]) or not C.SHA_RE.match(argv[1]) or not (argv[2] == "-" or C.SHA_RE.match(argv[2])):
        return fail(C.EXIT_POLICY, "usage: activate <release_id> <sha40> <from_sha40|-> [flags]")
    cmd = release_cmd("activate", "--id", argv[0], "--expect-sha", argv[1])
    if argv[2] != "-":
        cmd += ["--from-sha", argv[2]]
    for flag in argv[3:]:
        if flag == "allow-migrations":
            cmd.append("--allow-migrations")
        elif flag == "dry-run":
            cmd.append("--dry-run")
        elif flag.startswith("destructive-ack=") and PHRASE_RE.match(flag.split("=", 1)[1]):
            cmd += ["--destructive-ack", flag.split("=", 1)[1]]
        else:
            return fail(C.EXIT_POLICY, "unknown flag: %s" % flag[:40])
    return start_or_resume(cfg, given, cmd)


def v_rollback(cfg, argv):
    given, argv = split_run_flag(argv)
    target = argv[0] if argv else "previous"
    if target != "previous" and not C.RELEASE_ID_RE.match(target):
        return fail(C.EXIT_POLICY, "usage: rollback [<release_id>|previous] [unsafe-ack=<phrase>]")
    cmd = release_cmd("rollback", "--to", target)
    for flag in argv[1:]:
        if flag.startswith("unsafe-ack=") and PHRASE_RE.match(flag.split("=", 1)[1]):
            cmd += ["--unsafe-ack", flag.split("=", 1)[1]]
        else:
            return fail(C.EXIT_POLICY, "unknown flag: %s" % flag[:40])
    return start_or_resume(cfg, given, cmd)


def v_follow(cfg, argv):
    if len(argv) != 2 or not argv[1].isdigit():
        return fail(C.EXIT_POLICY, "usage: follow <run_id> <offset>")
    rd = run_dir(cfg, argv[0])
    offset = int(argv[1])
    logfile = rd / "log"
    data = b""
    if logfile.exists():
        with open(str(logfile), "rb") as fh:
            fh.seek(offset)
            data = fh.read(MAX_FOLLOW_BYTES)
    sys.stdout.write(C.redact(data.decode("utf-8", "replace")))
    sys.stdout.flush()
    new_offset = offset + len(data)
    exit_file = rd / "exit_code"
    if exit_file.exists() and len(data) < MAX_FOLLOW_BYTES:
        out("OFFSET %d" % new_offset)
        out("STATE done %s" % exit_file.read_text(encoding="utf-8").strip())
        return 0
    pid_file = rd / "pid"
    alive = False
    if pid_file.exists():
        try:
            alive = pid_alive(int(pid_file.read_text(encoding="utf-8").strip()))
        except ValueError:
            alive = False
    out("OFFSET %d" % new_offset)
    if alive or len(data) >= MAX_FOLLOW_BYTES or exit_file.exists():
        out("STATE running")
    else:
        time.sleep(1.5)                       # a supervisor that just finished may not have flushed exit_code yet
        if exit_file.exists():
            out("STATE done %s" % exit_file.read_text(encoding="utf-8").strip())
        else:
            out("STATE died")
    return 0


def v_result(cfg, argv):
    if len(argv) != 1:
        return fail(C.EXIT_POLICY, "usage: result <run_id>")
    p = run_dir(cfg, argv[0]) / "result.json"
    out(C.redact(p.read_text(encoding="utf-8")) if p.exists() else "{}")
    return 0


def v_status(cfg, argv):
    if argv:
        return fail(C.EXIT_POLICY, "status takes no arguments")
    proc = subprocess.run(release_cmd("status"))
    return proc.returncode


def v_version(cfg, argv):
    if argv:
        return fail(C.EXIT_POLICY, "version takes no arguments")
    out(json.dumps({"tooling": tool_version(), "python": "%d.%d.%d" % sys.version_info[:3]}))
    return 0


def v_backup_receipt(cfg, argv):
    if argv or cfg.get("backup_mode") != "laptop":
        return fail(C.EXIT_POLICY, "backup-receipt requires laptop backup mode and no arguments")
    import laptop_receipt
    try:
        reason = laptop_receipt.import_bundle(cfg, sys.stdin.buffer.read(131073))
    except ValueError as exc:
        return fail(C.EXIT_POLICY, str(exc))
    out(json.dumps({"ok": True, "message": reason}))
    return 0


VERBS = {"backup-receipt": v_backup_receipt, "version": v_version, "status": v_status, "stage": v_stage, "activate": v_activate, "rollback": v_rollback,
         "follow": v_follow, "result": v_result, "upload-offset": v_upload_offset,
         "upload-chunk": v_upload_chunk, "stage-upload": v_stage_upload}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--supervise":
        return supervise(argv[1], argv[2:])
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if not original:
        return fail(C.EXIT_POLICY, "interactive logins are not allowed; use a deploy verb")
    try:
        words = shlex.split(original)
    except ValueError:
        return fail(C.EXIT_POLICY, "unparseable command")
    if not words or words[0] not in VERBS:
        return fail(C.EXIT_POLICY, "unknown verb %r (allowed: %s)" % (words[0][:30] if words else "", ", ".join(sorted(VERBS))))
    try:
        cfg = C.Config()
        return VERBS[words[0]](cfg, words[1:])
    except C.ConfigError as exc:
        return fail(C.EXIT_POLICY, "configuration error: %s" % exc)
    except ValueError as exc:
        return fail(C.EXIT_POLICY, str(exc))


if __name__ == "__main__":
    sys.exit(main())
