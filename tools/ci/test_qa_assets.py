import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from qa_assets import ASSETS, TARGET, restore


class QAArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ASSETS).mkdir(parents=True)
        self.payload = {"1/a.jpg": b"original-qa-bytes-a", "2/b.jpg": b"original-qa-bytes-b"}
        self.build()

    def build(self, *, names=None):
        archive = self.root / ASSETS / "corpus.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for name, data in (names or self.payload).items():
                z.writestr(name, data)
        manifest = {"schema": 1, "archives": [{"name": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}],
                    "files": {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in self.payload.items()}}
        (self.root / ASSETS / "manifest.json").write_text(json.dumps(manifest))

    def test_exact_bytes_and_idempotence(self):
        self.assertEqual(restore(self.root), 2)
        self.assertEqual(restore(self.root), 2)
        for name, data in self.payload.items():
            self.assertEqual((self.root / TARGET / name).read_bytes(), data)

    def test_archive_corruption_is_refused_before_writes(self):
        (self.root / ASSETS / "corpus.zip").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            restore(self.root)
        self.assertFalse((self.root / TARGET).exists())

    def test_missing_member_is_refused_before_writes(self):
        self.build(names={"1/a.jpg": self.payload["1/a.jpg"]})
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            restore(self.root)
        self.assertFalse((self.root / TARGET).exists())

    def test_traversal_is_refused(self):
        self.build(names={"../escape.jpg": b"escape"})
        with self.assertRaisesRegex(ValueError, "Invalid"):
            restore(self.root)
        self.assertFalse((self.root / "escape.jpg").exists())

    def test_image_digest_mismatch_is_refused_before_writes(self):
        self.build(names={"1/a.jpg": b"different-qa-bytes!", "2/b.jpg": self.payload["2/b.jpg"]})
        with self.assertRaisesRegex(ValueError, "checksum"):
            restore(self.root)
        self.assertFalse((self.root / TARGET).exists())

    def test_existing_edited_file_is_preserved(self):
        p = self.root / TARGET / "1/a.jpg"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"local edits")
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            restore(self.root)
        self.assertEqual(p.read_bytes(), b"local edits")

    def test_symlink_destination_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        p = self.root / TARGET
        p.parent.mkdir(parents=True)
        p.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            restore(self.root)
        self.assertEqual(list(outside.iterdir()), [])
