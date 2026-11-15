#!/usr/bin/env python3
"""Export a Git baseline's exact source hashes for one-time server registration."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

SOURCE_ROOTS = ("apps", "deploy", "shop_core", "static", "templates", "manage.py", "requirements.txt")


def make_manifest(repo, sha):
    resolved = subprocess.check_output(["git", "-C", str(repo), "rev-parse", sha + "^{commit}"], text=True).strip()
    data = subprocess.check_output(["git", "-C", str(repo), "archive", resolved, "--", *SOURCE_ROOTS])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for item in tar:
            if item.isfile():
                files[item.name] = hashlib.sha256(tar.extractfile(item).read()).hexdigest()
            elif not item.isdir():
                raise ValueError("baseline contains links / special files")
    return {"schema": 1, "sha": resolved, "files": files}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=".")
    p.add_argument("--sha", default="86742072855eb0d937a63be0c7662919d0dbef87")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    manifest = make_manifest(a.repo, a.sha)
    Path(a.out).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("BASELINE_MANIFEST_WRITTEN files=%d sha=%s" % (len(manifest["files"]), manifest["sha"]))
