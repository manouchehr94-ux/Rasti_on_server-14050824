"""Keep request evidence off until the host operator explicitly arms it."""
import os
from pathlib import Path
import stat

ACTIVATION_MARKER = Path("/etc/rastisi/availability-evidence.enabled")


def evidence_enabled():
    if os.environ.get("RASTISI_AVAILABILITY_EVIDENCE") != "1":
        return False
    try:
        marker = ACTIVATION_MARKER.lstat()
    except OSError:
        return False
    return stat.S_ISREG(marker.st_mode) and marker.st_uid == 0 and not marker.st_mode & 0o022
