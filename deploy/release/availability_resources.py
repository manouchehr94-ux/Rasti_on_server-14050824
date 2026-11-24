#!/usr/bin/env python3
"""Emit one private aggregate snapshot. No application imports, keys, or SQL text."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

SERVICE = "rastisi-rc-20261111.service"
CGROUP = Path("/sys/fs/cgroup")
PG_SQL = """SELECT count(*) FILTER (WHERE backend_type='client backend'),
count(*) FILTER (WHERE backend_type='client backend' AND pid <> pg_backend_pid() AND state='active'),
count(*) FILTER (WHERE backend_type='client backend' AND wait_event_type='Lock'),
count(*) FILTER (WHERE backend_type='client backend' AND pid <> pg_backend_pid()
AND state='active' AND now()-query_start > interval '1 second'),
current_setting('max_connections')::int FROM pg_catalog.pg_stat_activity"""
REDIS_FIELDS = {"used_memory", "maxmemory", "connected_clients", "blocked_clients",
                "rejected_connections", "evicted_keys", "instantaneous_ops_per_sec"}


def command(argv, *, env=None, data=None):
    try:
        result = subprocess.run(argv, env=env, input=data, text=True, capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def read(path):
    try:
        return path.read_text()
    except OSError:
        return ""


def counters(text, wanted, separator=None):
    result = {}
    for line in text.splitlines():
        parts = line.split(separator, 1)
        if len(parts) == 2 and parts[0] in wanted:
            value = parts[1].strip().removesuffix(" kB").strip()
            if re.fullmatch(r"[0-9]{1,20}", value):
                result[parts[0]] = int(value)
    return result


def cgroups():
    group = command(["systemctl", "show", SERVICE, "-p", "ControlGroup", "--value"]).strip()
    path = (CGROUP / group.lstrip("/")).resolve()
    if not group.startswith("/") or not path.is_relative_to(CGROUP) or not path.is_dir():
        return None
    result = []
    for scope in (path, *path.parents):
        if not scope.is_relative_to(CGROUP):
            break
        memory = {name: value for name in ("memory.current", "memory.max", "memory.high")
                  if re.fullmatch(r"(?:[0-9]{1,20}|max)", value := read(scope / name).strip())}
        result.append({"memory_bytes": memory, "oom_local": counters(
            read(scope / "memory.events.local"), {"oom", "oom_kill", "high", "max"}),
            "cpu": counters(read(scope / "cpu.stat"), {"usage_usec", "nr_throttled", "throttled_usec"})})
    return result


def postgres():
    service = os.environ.get("RASTISI_EVIDENCE_PGSERVICE", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", service):
        return None
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(PGSERVICE=service, PGCONNECT_TIMEOUT="2",
               PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=1000 -c lock_timeout=500")
    row = command(["psql", "-XqAtw", "-v", "ON_ERROR_STOP=1", "-f", "-"], env=env,
                  data=f"BEGIN READ ONLY;\n{PG_SQL};\nROLLBACK;\n").strip()
    if not re.fullmatch(r"[0-9]{1,10}(?:\|[0-9]{1,10}){4}", row):
        return None
    return dict(zip(("clients_including_probe", "active_others", "lock_waiters", "active_over_1s",
                     "max_connections"), map(int, row.split("|")), strict=True))


def redis():
    socket = os.environ.get("RASTISI_EVIDENCE_REDIS_SOCKET", "")
    if not socket.startswith("/"):
        return None
    return counters(command(["redis-cli", "-s", socket, "--raw", "INFO", "clients", "memory", "stats"]),
                    REDIS_FIELDS, ":") or None


def snapshot():
    cpu = read(Path("/proc/stat")).splitlines()
    ticks = cpu[0].split()[1:9] if cpu and cpu[0].startswith("cpu ") else []
    return {"format": "rastisi_resources_v1", "unix_ms": time.time_ns() // 1_000_000,
            "cpu": dict(zip(("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"),
                            map(int, ticks))),
            "ram_kib": counters(read(Path("/proc/meminfo")), {"MemAvailable", "SwapFree"}, ":"),
            "cgroups_service_to_root": cgroups(), "postgres": postgres(), "redis": redis()}


if __name__ == "__main__":
    print(json.dumps(snapshot(), separators=(",", ":")))
