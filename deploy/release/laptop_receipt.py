"""Verify laptop-signed backup metadata. No backup contents are stored on the server."""
import base64
import calendar
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

import rastisi_common as C


def signers_trusted(path):
    # A trusted file in a deploy-writable parent would be replaceable.
    if path.resolve() != path:
        return False
    for entry in (path, *path.parents):
        mode = entry.stat()
        if mode.st_uid != 0 or mode.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False
    return True


def verify(cfg, bundle, from_sha=None, to_sha=None, now=None):
    """Fail closed on signature, freshness, database or deployment mismatch."""
    signers = Path(cfg.get("backup_allowed_signers", "/etc/rastisi/backup_allowed_signers"))
    try:
        if not signers_trusted(signers):
            return False, "backup signers must be root-owned and not group/world writable"
        payload = base64.b64decode(bundle["payload"], validate=True)
        if len(payload) > 65536:
            return False, "backup receipt is too large"
        with tempfile.TemporaryDirectory(prefix="rastisi-receipt-") as tmp:
            sig = Path(tmp) / "receipt.sig"
            sig.write_text(bundle["signature"], encoding="ascii")
            proc = subprocess.run([
                "ssh-keygen", "-Y", "verify", "-f", str(signers),
                "-I", "rastisi-laptop", "-n", "rastisi-backup", "-s", str(sig),
            ], input=payload, capture_output=True, timeout=15)
        if proc.returncode:
            return False, "laptop backup signature is invalid"
        info = json.loads(payload)
        if not isinstance(info, dict) or info.get("schema") != 1 or info.get("storage") != "laptop":
            return False, "not a laptop backup receipt"
        for key, wanted in (("from_sha", from_sha), ("to_sha", to_sha)):
            if not C.SHA_RE.fullmatch(info.get(key, "")) or (wanted and info[key] != wanted):
                return False, "backup receipt does not match " + key
        if info.get("database") != cfg.get("backup_database", "rastisi_rc_20261111"):
            return False, "backup database mismatch"
        for key in ("database_sha256", "media_archive_sha256", "media_manifest_sha256"):
            if not C.SHA256_RE.fullmatch(info.get(key, "")):
                return False, "missing backup checksum: " + key
        if info.get("dump_readable") is not True or info.get("media_verified") is not True:
            return False, "database format / media verification did not pass"
        if type(info.get("media_files")) is not int or info["media_files"] < 0:
            return False, "invalid media count"
        finished = calendar.timegm(time.strptime(info["finished_at"], "%Y-%m-%dT%H:%M:%SZ"))
        age = ((time.time() if now is None else now) - finished) / 60
        if age < -5 or age > cfg.int("backup_max_age_minutes", 90):
            return False, "laptop backup is expired or future-dated"
        return True, "signed laptop backup verified (%d min old); full DB restore is not asserted" % age
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError):
        return False, "laptop backup receipt cannot be verified"


def import_bundle(cfg, data):
    if len(data) > 131072:
        raise ValueError("receipt is too large")
    bundle = json.loads(data)
    ok, reason = verify(cfg, bundle)
    if not ok:
        raise ValueError(reason)
    cfg.state.mkdir(parents=True, exist_ok=True)
    C.atomic_write_json(cfg.state / "laptop-backup.json", bundle, mode=0o644)
    return reason
