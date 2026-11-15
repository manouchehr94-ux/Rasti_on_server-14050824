#!/usr/bin/env python3
"""Run ON THE LAPTOP. Binary SSH streams go directly to local files (Windows-safe)."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import time


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def checked_manifest(archive):
    """Hash archived media without extracting private files or following links."""
    result = {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = member.name.removeprefix("./")
            parts = Path(name).parts
            if not parts or parts[0] not in ("media", "private_media") or ".." in parts or name.startswith("/"):
                raise ValueError("unexpected archive path")
            if member.isdir():
                continue
            if not member.isfile() or name in result:
                raise ValueError("non-regular / duplicate archive entry")
            h = hashlib.sha256()
            with tar.extractfile(member) as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            result[name] = h.hexdigest()
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ssh", default="ssh root@87.107.9.4")
    ap.add_argument("--destination", required=True)
    ap.add_argument("--from-sha", required=True)
    ap.add_argument("--to-sha", required=True)
    ap.add_argument("--signing-key", required=True)
    ap.add_argument("--database", default="rastisi_rc_20261111")
    ap.add_argument("--pg-restore", default="pg_restore", help="local PostgreSQL pg_restore executable (same/newer major as server)")
    ap.add_argument("--pg-restore-image", default="", help="use Docker for pg_restore, e.g. postgres:16, when no local PostgreSQL client is installed")
    args = ap.parse_args()
    import re
    if not all(re.fullmatch("[0-9a-f]{40}", v) for v in (args.from_sha, args.to_sha)):
        raise SystemExit("full commit ids required")
    dest = Path(args.destination)
    dest.mkdir(parents=True, exist_ok=False)
    ssh = shlex.split(args.ssh) + ["-T"]
    endpoint = "/usr/bin/python3 /usr/local/lib/rastisi/laptop_backup_source.py "

    def read_manifest():
        p = subprocess.run(ssh + [endpoint + "manifest"], capture_output=True, check=True)
        return json.loads(p.stdout)

    before = read_manifest()
    for verb, filename in (("database", "database.dump"), ("media", "media.tar.gz")):
        print("Streaming", filename, "to laptop", flush=True)
        with (dest / filename).open("xb") as output:
            subprocess.run(ssh + [endpoint + verb], stdout=output, check=True)
    # This validates archive readability, not a complete database restore drill.
    if args.pg_restore_image:
        subprocess.run(["docker", "run", "--rm", "--mount",
                        "type=bind,source=%s,target=/backup,readonly" % dest.resolve(),
                        args.pg_restore_image, "pg_restore", "--file=/dev/null", "/backup/database.dump"], check=True)
    else:
        subprocess.run([args.pg_restore, "--file=" + str(dest / "dump-validation.sql"), str(dest / "database.dump")], check=True)
        (dest / "dump-validation.sql").unlink()
    after = read_manifest()
    if before != after or checked_manifest(dest / "media.tar.gz") != after:
        raise SystemExit("media changed or failed SHA256 verification; no receipt issued")
    media_json = json.dumps(after, sort_keys=True).encode()
    (dest / "media-sha256.json").write_bytes(media_json)
    info = {
        "schema": 1, "storage": "laptop", "database": args.database,
        "from_sha": args.from_sha, "to_sha": args.to_sha,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "database_sha256": digest(dest / "database.dump"),
        "media_archive_sha256": digest(dest / "media.tar.gz"),
        "media_manifest_sha256": hashlib.sha256(media_json).hexdigest(),
        "media_files": len(after), "dump_readable": True, "media_verified": True,
        "full_restore_tested": False,
    }
    payload = json.dumps(info, sort_keys=True).encode()
    receipt = dest / "receipt.json"
    receipt.write_bytes(payload)
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", args.signing_key, "-n", "rastisi-backup", str(receipt)], check=True)
    bundle = {
        "payload": base64.b64encode(payload).decode(),
        "signature": Path(str(receipt) + ".sig").read_text(encoding="ascii"),
    }
    data = json.dumps(bundle).encode()
    (dest / "receipt-bundle.json").write_bytes(data)
    subprocess.run(ssh + [endpoint + "receipt"], input=data, check=True)
    print("LAPTOP_BACKUP_VERIFIED_RECEIPT_IMPORTED files=%d" % len(after))


if __name__ == "__main__":
    main()
