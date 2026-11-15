#!/usr/bin/env python3
"""Administrator-only streaming backup endpoint; never creates server backup files."""
import json
import os
from pathlib import Path
import subprocess
import sys

import rastisi_common as C


def media_roots(cfg):
    env = C.parse_env_file(cfg.get("env_file"))
    result = {}
    for label, variable in (("media", "DJANGO_MEDIA_ROOT"), ("private_media", "DJANGO_PRIVATE_MEDIA_ROOT")):
        path = Path(env[variable])
        expected = cfg.base / "shared" / label
        if path != expected or path.is_symlink() or not path.is_dir():
            raise ValueError("unexpected shared media path")
        result[label] = path
    return result


def manifest(cfg):
    result = {}
    for label, root in media_roots(cfg).items():
        for p in sorted(root.rglob("*")):
            if p.is_symlink():
                raise ValueError("media symlinks require a separately reviewed backup method")
            if p.is_file():
                result[label + "/" + p.relative_to(root).as_posix()] = C.sha256_file(p)
    return result


def main():
    if os.geteuid() != 0:
        raise SystemExit("administrator SSH only; never expose this endpoint to the deploy key")
    cfg = C.Config()
    verb = sys.argv[1] if len(sys.argv) == 2 else ""
    if verb == "manifest":
        print(json.dumps(manifest(cfg), sort_keys=True))
        return 0
    if verb == "media":
        media_roots(cfg)
        return subprocess.call(["tar", "-czf", "-", "-C", str(cfg.base / "shared"), "media", "private_media"])
    if verb == "database":
        from urllib.parse import urlparse, unquote
        env = C.parse_env_file(cfg.get("env_file"))
        database = cfg.get("backup_database", "rastisi_rc_20261111")
        target = urlparse(env["DATABASE_URL"])
        if (target.scheme not in ("postgres", "postgresql") or
                target.hostname not in (None, "localhost", "127.0.0.1", "::1") or
                target.port not in (None, 5432) or unquote(target.path.lstrip("/")) != database):
            raise ValueError("this backup endpoint requires the configured local PostgreSQL database on port 5432")
        return subprocess.call(["runuser", "-u", "postgres", "--", "/usr/bin/pg_dump",
                                "--host=/var/run/postgresql", "--port=5432", "--username=postgres",
                                "-Fc", "--no-owner", "--no-privileges", "-d", database])
    if verb == "receipt":
        # Never let root write receipt files inside the deploy-owned tree.
        data = sys.stdin.buffer.read(131073)
        return subprocess.run([
            "runuser", "-u", "rastisi-deploy", "--", "/usr/bin/env",
            "SSH_ORIGINAL_COMMAND=backup-receipt", "/usr/bin/python3",
            "/usr/local/lib/rastisi/rastisi_gate.py",
        ], input=data).returncode
    raise SystemExit("allowed: manifest, media, database, receipt")


if __name__ == "__main__":
    raise SystemExit(main())
