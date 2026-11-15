"""Restore the exact owner-supplied QA corpus from verified repository archives.

Archives remain repository-only. No network, generated substitute images, or
production paths are involved. Existing edited files are never overwritten.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

ASSETS = Path("tools/ci/assets")
TARGET = Path("apps/stores/demo_assets/rasti_mode_demo/raw_user_catalog")


def restore(root):
    root = Path(root)
    manifest = json.loads((root / ASSETS / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported QA asset manifest")
    expected = manifest["files"]
    decoded = {}
    total = 0
    for item in manifest["archives"]:
        name = item["name"]
        if Path(name).name != name:
            raise ValueError("Invalid QA archive name")
        archive = root / ASSETS / name
        if hashlib.sha256(archive.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("QA archive checksum mismatch: " + name)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                rel = PurePosixPath(info.filename)
                if (rel.is_absolute() or len(rel.parts) != 2 or rel.parts[0] not in "12345"
                        or len(rel.parts[0]) != 1 or rel.suffix != ".jpg"
                        or ".." in rel.parts or "\\" in info.filename or ":" in info.filename
                        or info.filename not in expected or info.filename in decoded
                        or (info.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("Invalid/duplicate QA archive entry")
                record = expected[info.filename]
                total += info.file_size
                if info.file_size != record["size"] or total > 25 * 1024 * 1024:
                    raise ValueError("Unexpected QA asset size")
                data = z.read(info)
                if hashlib.sha256(data).hexdigest() != record["sha256"]:
                    raise ValueError("QA image checksum mismatch")
                decoded[info.filename] = data
    if set(decoded) != set(expected):
        raise ValueError("Incomplete QA archive corpus")
    target = root / TARGET
    # Validate all destinations before writing, including edited existing files.
    for rel, data in decoded.items():
        dest = target / rel
        for ancestor in [target, *target.parents]:
            if ancestor == root:
                break
            if ancestor.is_symlink():
                raise ValueError("Symlink in QA destination")
        if dest.parent.is_symlink() or dest.is_symlink():
            raise ValueError("Symlink in QA destination")
        if dest.exists() and (not dest.is_file() or dest.read_bytes() != data):
            raise ValueError("Existing QA asset differs; refusing to overwrite: " + rel)
    for rel, data in decoded.items():
        dest = target / rel
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".qa-", dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(temporary, dest)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return len(decoded)


if __name__ == "__main__":
    print("QA_ASSETS_SHA256_VERIFIED files=%d" % restore(Path(__file__).resolve().parents[2]))
