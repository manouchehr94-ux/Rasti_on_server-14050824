#!/usr/bin/env python3
"""Collect allowlisted Linux/systemd capacity metrics. No configuration execution."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")


def read(path):
    try:
        return path.read_text()
    except (OSError, UnicodeError):
        return ""


def number(value):
    return int(value) if value.isascii() and value.isdecimal() else None


def fields(path, keys):
    result = {}
    for line in read(path).splitlines():
        key, _, value = line.partition(":")
        parts = value.split()
        if key in keys and parts:
            result[key] = number(parts[0])
    return result


def unit_state(unit):
    properties = ("MainPID", "NRestarts", "TasksCurrent", "ControlGroup", "ActiveState")
    try:
        result = subprocess.run(
            ["systemctl", "show", *(f"--property={key}" for key in properties), "--", unit],
            capture_output=True, text=True, timeout=8, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def process(pid):
    base = PROC / str(pid)
    status = fields(base / "status", {"Threads", "VmRSS"})
    return {
        "tasks": status.get("Threads"),
        "rss_kib": status.get("VmRSS"),
        "pss_kib": fields(base / "smaps_rollup", {"Pss"}).get("Pss"),
    }


def children(pid):
    result = []
    for entry in PROC.iterdir():
        child = number(entry.name)
        if child is not None and fields(entry / "status", {"PPid"}).get("PPid") == pid:
            result.append(child)
    return sorted(result)


def levels(group):
    if not (CGROUP / "cgroup.controllers").is_file() or not group.startswith("/"):
        return None
    directory = (CGROUP / group.lstrip("/")).resolve()
    if not directory.is_relative_to(CGROUP) or not directory.is_dir():
        return None
    result = []
    while True:
        row = {}
        for key in ("memory.current", "memory.peak", "memory.max", "memory.high", "pids.current", "pids.max"):
            value = read(directory / key).strip()
            row[key] = "max" if value == "max" else number(value)
        quota = read(directory / "cpu.max").split()
        row["cpu_quota_cores"] = (
            "max" if quota[0] == "max" else int(quota[0]) / int(quota[1])
        ) if len(quota) == 2 else None
        for filename, allowed in (
            ("cpu.stat", {"usage_usec", "nr_periods", "nr_throttled", "throttled_usec"}),
            ("memory.events", {"high", "max", "oom", "oom_kill"}),
        ):
            metrics = {}
            for line in read(directory / filename).splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] in allowed:
                    metrics[parts[0]] = number(parts[1])
            row[filename] = metrics
        result.append(row)
        if directory == CGROUP:
            return result
        directory = directory.parent


def snapshot(unit):
    status = unit_state(unit)
    pid = number(status.get("MainPID", "")) or None
    try:
        allowed_cpu = len(os.sched_getaffinity(pid)) if pid else None
    except OSError:
        allowed_cpu = None
    child_pids = children(pid) if pid else []
    cpu_row = read(PROC / "stat").splitlines()
    ticks = cpu_row[0].split()[1:9] if cpu_row and cpu_row[0].startswith("cpu ") else []
    busy = sum(int(ticks[i]) for i in (0, 1, 2, 5, 6, 7)) if len(ticks) == 8 else None
    return {
        "service_active": status.get("ActiveState") == "active",

        "master": process(pid) if pid else None,
        "service_restarts": number(status.get("NRestarts", "")),
        "service_tasks": number(status.get("TasksCurrent", "")),
        "master_direct_child_count": len(child_pids) if pid else None,
        "master_direct_children": [process(child) for child in child_pids],
        "master_cpu_affinity_count": allowed_cpu,
        "host_cpu_ticks_total": sum(map(int, ticks)) if ticks else None,
        "host_cpu_ticks_busy": busy,
        "host_memory_kib": fields(PROC / "meminfo", {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}),
        "cgroup_v2_service_to_root": levels(status.get("ControlGroup", "")),
    }


def main():
    parser = argparse.ArgumentParser(add_help=True, exit_on_error=False)
    parser.add_argument("--unit", default="rastisi-rc-20261111.service")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--verified-workers", type=int)
    parser.add_argument("--verified-threads", type=int)
    parser.add_argument("--verified-timeout", type=int)
    parser.add_argument("--verified-worker-class", choices=("sync", "gthread", "gevent", "eventlet", "tornado", "other"))
    try:
        args, unknown = parser.parse_known_args()
        valid = (
            not unknown and re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", args.unit)
            and not args.unit.startswith("-")
            and 1 <= args.samples <= 1000 and 1 <= args.interval <= 3600
            and all(value is None or value > 0 for value in (args.verified_workers, args.verified_threads))
            and (args.verified_timeout is None or args.verified_timeout >= 0)
        )
    except argparse.ArgumentError:
        valid = False
    if not valid:
        print('{"error":"invalid_arguments"}')
        return 2
    for index in range(args.samples):
        result = snapshot(args.unit)
        result["sample"] = index + 1
        result["operator_verified_effective_settings"] = {
            "workers": args.verified_workers, "threads": args.verified_threads,
            "worker_class": args.verified_worker_class, "timeout_seconds": args.verified_timeout,
        }
        print(json.dumps(result), flush=True)
        if result["master_direct_child_count"] is None:
            return 1
        if index + 1 < args.samples:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print('{"error":"collection_failed"}')
        raise SystemExit(1)
