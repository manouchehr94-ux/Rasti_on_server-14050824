#!/usr/bin/env python3
"""Atomically exchange installed tooling with a verified sibling on Linux."""

import argparse
import ctypes
import os
from pathlib import Path
import stat


LIB = Path("/usr/local/lib")


def check_tree(root, version):
    for path in (root, *root.rglob("*")):
        info = path.lstat()
        if (
            info.st_uid != 0 or info.st_mode & 0o022
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        ):
            raise ValueError(f"unsafe tooling entry: {path}")
    if not root.is_dir() or (root / "TOOLING_VERSION").read_text().strip() != version:
        raise ValueError(f"unexpected tooling version: {root}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sibling", help="Name of a verified tooling directory beside rastisi")
    parser.add_argument("installed_version")
    parser.add_argument("candidate_version")
    args = parser.parse_args()
    if (
        os.geteuid() != 0 or not args.sibling.startswith("rastisi-tools-")
        or Path(args.sibling).name != args.sibling
    ):
        parser.error("run as root with one rastisi-tools-* sibling name")
    installed, candidate = LIB / "rastisi", LIB / args.sibling
    try:
        for parent in (LIB, *LIB.parents):
            info = parent.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"unsafe tooling parent: {parent}")
        check_tree(installed, args.installed_version)
        check_tree(candidate, args.candidate_version)
        if (installed.stat().st_dev != candidate.stat().st_dev
                or not (candidate / "rastisi_gate.py").is_file()):
            raise ValueError("candidate must be on the same filesystem and contain the gate")
        swap = ctypes.CDLL(None, use_errno=True).renameat2
        swap.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        swap.restype = ctypes.c_int
        if swap(-100, os.fsencode(installed), -100, os.fsencode(candidate), 2):
            raise OSError(os.strerror(ctypes.get_errno()))
        fd = os.open(LIB, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (AttributeError, OSError, ValueError) as exc:
        parser.exit(1, f"Inspect tooling versions before retry: {exc}\n")
    print(f"Installed {args.candidate_version}; previous tooling retained at {candidate}")


if __name__ == "__main__":
    main()
