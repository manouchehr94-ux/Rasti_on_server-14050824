import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap_existing
import build_release
import laptop_backup
import laptop_receipt
import rastisi_common as C
import rastisi_release
import runtime_check
import rastisi_app_task


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True)
        self.signers = self.root / "allowed"
        self.signers.write_text('rastisi-laptop namespaces="rastisi-backup" ' + Path(str(self.key) + ".pub").read_text())
        conf = self.root / "deploy.conf"
        conf.write_text("[rastisi]\nbase_dir = %s\napp_user = tester\nservice = rastisi.service\nenv_file = /unused\nport = 18082\nbackup_mode = laptop\nbackup_allowed_signers = %s\n" % (self.root / "srv", self.signers))
        self.cfg = C.Config(str(conf))
        self.info = {
            "schema": 1, "storage": "laptop", "from_sha": "a" * 40,
            "to_sha": "b" * 40, "database": "rastisi_rc_20261111",
            "database_sha256": "c" * 64, "media_archive_sha256": "d" * 64,
            "media_manifest_sha256": "e" * 64, "dump_readable": True,
            "media_verified": True, "media_files": 1028,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def bundle(self, info=None):
        payload = json.dumps(info or self.info).encode()
        receipt = self.root / "payload.json"
        receipt.write_bytes(payload)
        Path(str(receipt) + ".sig").unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(self.key), "-n", "rastisi-backup", str(receipt)], check=True, capture_output=True)
        return {"payload": base64.b64encode(payload).decode(), "signature": Path(str(receipt) + ".sig").read_text()}

    def verify(self, bundle, **kwargs):
        # Cryptography is real; trust of /etc root ownership is tested separately.
        with patch.object(laptop_receipt, "signers_trusted", return_value=True):
            return laptop_receipt.verify(self.cfg, bundle, **kwargs)

    def test_real_ed25519_signature_and_exact_deployment_pair(self):
        self.assertTrue(self.verify(self.bundle(), from_sha="a"*40, to_sha="b"*40)[0])
        self.assertFalse(self.verify(self.bundle(), from_sha="f"*40)[0])
        self.assertFalse(self.verify(self.bundle(), to_sha="f"*40)[0])

    def test_unsigned_modified_receipt_is_rejected(self):
        bundle = self.bundle()
        changed = dict(self.info, to_sha="f"*40)
        bundle["payload"] = base64.b64encode(json.dumps(changed).encode()).decode()
        self.assertFalse(self.verify(bundle)[0])

    def test_other_signing_key_is_rejected(self):
        bundle = self.bundle()
        other = self.root / "other"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
        self.signers.write_text("rastisi-laptop " + Path(str(other) + ".pub").read_text())
        self.assertFalse(self.verify(bundle)[0])

    def test_expired_future_unverified_wrong_database_or_storage_refused(self):
        cases = [dict(database="rasti_service"), dict(storage="server"),
                 dict(media_verified=False), dict(dump_readable=False),
                 dict(database_sha256=""), dict(finished_at="2000-01-01T00:00:00Z"),
                 dict(finished_at="2099-01-01T00:00:00Z")]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertFalse(self.verify(self.bundle(dict(self.info, **changes)))[0])

    def test_untrusted_parent_is_refused(self):
        self.assertFalse(laptop_receipt.signers_trusted(self.signers))
        self.assertFalse(laptop_receipt.verify(self.cfg, self.bundle())[0])

    def test_import_does_not_store_backup_payloads(self):
        data = json.dumps(self.bundle()).encode()
        with patch.object(laptop_receipt, "signers_trusted", return_value=True):
            laptop_receipt.import_bundle(self.cfg, data)
        self.assertEqual([p.name for p in self.cfg.state.iterdir()], ["laptop-backup.json"])

    def test_missing_receipt_blocks_even_old_skip_ack(self):
        from types import SimpleNamespace
        cfg = self.cfg
        args = SimpleNamespace(from_sha="a"*40, skip_backup_check_ack=True)
        with self.assertRaises(rastisi_release.Abort):
            rastisi_release.migration_policy(cfg, SimpleNamespace(dir=cfg.state), SimpleNamespace(say=lambda *_: None),
                ["core.0002"], {"level": "ADDITIVE"}, {"sha": "b"*40}, args)

    def test_code_only_deploy_does_not_require_a_laptop(self):
        from types import SimpleNamespace
        rastisi_release.migration_policy(self.cfg, SimpleNamespace(dir=self.cfg.state),
            SimpleNamespace(say=lambda *_: None), [], {"level": "NONE"}, {"sha": "b"*40}, SimpleNamespace())

    def test_migration_receipt_uses_actual_current_commit_not_caller_claim(self):
        from types import SimpleNamespace
        rid = "legacy-" + "a"*12
        release = self.cfg.releases / rid
        release.mkdir(parents=True)
        (release / "release.json").write_text(json.dumps({"sha": "a"*40}))
        self.cfg.current_link.symlink_to("releases/" + rid)
        self.cfg.state.mkdir()
        (self.cfg.state / "laptop-backup.json").write_text(json.dumps(self.bundle()))
        params = (self.cfg, SimpleNamespace(dir=self.cfg.state), SimpleNamespace(say=lambda *_: None),
                  ["core.0002"], {"level": "ADDITIVE"}, {"sha": "b"*40},
                  SimpleNamespace(from_sha="a"*40, skip_backup_check_ack=True))
        with patch.object(laptop_receipt, "signers_trusted", return_value=True):
            rastisi_release.migration_policy(*params)
            (release / "release.json").write_text(json.dumps({"sha": "f"*40}))
            with self.assertRaises(rastisi_release.Abort):
                rastisi_release.migration_policy(*params)


class PackagingTests(unittest.TestCase):
    def test_static_publish_preserves_root_and_nginx_traversal_under_private_umask(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "source", Path(d) / "public"
            (src / "new-section" / "css").mkdir(parents=True)
            (src / "new-section" / "css" / "site.css").write_text("body{}")
            dst.mkdir(mode=0o750)
            dst.chmod(0o750)  # match production independently of the test runner umask
            previous = os.umask(0o077)
            try:
                self.assertEqual(rastisi_app_task._sync_tree(src, dst), 1)
            finally:
                os.umask(previous)
            self.assertEqual(dst.stat().st_mode & 0o777, 0o750)
            self.assertEqual((dst / "new-section").stat().st_mode & 0o777, 0o755)
            self.assertEqual((dst / "new-section/css").stat().st_mode & 0o777, 0o755)
            self.assertEqual((dst / "new-section/css/site.css").stat().st_mode & 0o777, 0o644)

    def test_runtime_rejects_glibc_older_than_the_upgraded_crypto_wheels(self):
        from types import SimpleNamespace
        have = {"python": "3.12", "machine": "x86_64", "system": "Linux", "libc": ["glibc", "2.31"]}
        with patch.object(runtime_check.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(have))):
            with self.assertRaisesRegex(ValueError, "glibc >= 2.34"):
                runtime_check.check_runtime("python", {"python": "3.12"})
        have["libc"][1] = "2.34"
        with patch.object(runtime_check.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(have))):
            self.assertEqual(runtime_check.check_runtime("python", {"python": "3.12"}), have)

    def test_runtime_assets_and_demo_fingerprint_inputs_survive(self):
        for name in ("manage.py", "requirements.txt", "apps/catalog/migrations/0001_initial.py",
                     "apps/stores/demo_assets/rasti_mode_demo/products/image.webp",
                     "apps/stores/demo_assets/rasti_mode_demo/curated/image.webp",
                     "apps/stores/demo_assets/rasti_mode_demo/selected_product_media_manifest.json",
                     "apps/stores/management/commands/seed_ready_template_fashion_demo.py",
                     "apps/core/static/fonts/licenses/OFL.txt", "deploy/cron/run_job.sh"):
            self.assertTrue(build_release.production_path(name), name)

    def test_repository_only_material_is_not_deployed(self):
        for name in ("docs/a.md", "tools/ci/test_gate.py", ".github/workflows/ci.yml",
                     ".env", ".venv/bin/python", "media/upload.jpg", "apps/blog/tests.py",
                     "apps/core/tests/test_a.py", "shop_core/settings_browser_tests.py",
                     "apps/core/__pycache__/a.pyc", "apps/storefront_builder/qa/reference_capture.py",
                     "apps/stores/demo_assets/rasti_mode_demo/raw_user_catalog/1.jpg",
                     "apps/stores/demo_assets/rasti_mode_demo/scripts/build_inventory.py",
                     "deploy/release/install.sh", "apps/billing/management/commands/qa_enable_payment_simulator.py"):
            self.assertFalse(build_release.production_path(name), name)

    def test_real_git_export_keeps_assets_and_drops_tests_and_tools(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo, dest = root / "repo", root / "out"
            repo.mkdir(); dest.mkdir()
            for name in ("manage.py", "apps/core/tests/test_a.py", "tools/ci/a.py", "apps/core/static/site.css"):
                p = repo / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("fixture")
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture"], check=True)
            sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            build_release.export_tree(repo, sha, dest)
            self.assertEqual(sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()),
                             ["apps/core/static/site.css", "manage.py"])

    def test_bootstrap_rejects_changed_or_extra_source_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").write_text("original")
            manifest = {"schema": 1, "sha": "a"*40, "files": {"manage.py": hashlib.sha256(b"original").hexdigest()}}
            bootstrap_existing.verify_baseline(root, manifest)
            (root / "manage.py").write_text("changed")
            with self.assertRaises(ValueError):
                bootstrap_existing.verify_baseline(root, manifest)

    def test_media_archive_hashes_binary_without_extracting(self):
        import io
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "media.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                data = b"\x00binary\xff"
                item = tarfile.TarInfo("media/example.bin"); item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
            self.assertEqual(laptop_backup.checked_manifest(archive), {"media/example.bin": hashlib.sha256(data).hexdigest()})

    def test_media_archive_traversal_or_links_are_refused(self):
        for name, kind in (("../etc/passwd", tarfile.REGTYPE), ("media/link", tarfile.SYMTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                archive = Path(d) / "media.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    item = tarfile.TarInfo(name); item.type = kind
                    if kind == tarfile.SYMTYPE: item.linkname = "/etc/passwd"
                    tar.addfile(item)
                with self.assertRaises(ValueError):
                    laptop_backup.checked_manifest(archive)


if __name__ == "__main__":
    unittest.main()
