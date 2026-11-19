#!/usr/bin/env python3
"""Print allowlisted, read-only incident evidence as JSON. Linux + systemd."""
import argparse
import json
import os
import re
import stat
import subprocess
from datetime import datetime
from pathlib import Path

SERVICE = "rastisi-rc-20261111.service"
RUNTIME = Path("/run/rastisi-gunicorn")
RELEASE_VENV = Path("/srv/rastisi/current/venv")
COUNTS = {
    "worker_timeout": re.compile(r"\bWORKER TIMEOUT \(pid:(\d{1,10})\)"),
    "worker_boot": re.compile(r"\bBooting worker with pid: (\d{1,10})"),
    "worker_kill": re.compile(r"\bWorker \(pid:(\d{1,10})\) was sent SIGKILL\b"),
    "control_permission": re.compile(r"Control server error: .*Permission denied"),
    "nginx_upstream_timeout": re.compile(r"upstream timed out"),
    "nginx_upstream_connect": re.compile(r"connect\(\) failed .*while connecting to upstream"),
    "kernel_oom": re.compile(r"(?:Out of memory: Killed process|oom-kill:)"),
}
FIELDS = ("MainPID", "NRestarts", "MemoryCurrent", "TasksCurrent", "ActiveState")


def read_command(*command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace",
                                timeout=25, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def journal(start, end, selector, keys):
    output = read_command("journalctl", "--no-pager", "-o", "json", "--since", start,
                          "--until", end, "-n", "10001", *selector)
    if output is None:
        return {"readable": False}
    lines = output.splitlines()
    events = []
    for line in lines[:10000]:
        try:
            record = json.loads(line)
            message = record.get("MESSAGE")
            stamp = record.get("__REALTIME_TIMESTAMP", "")
        except (ValueError, AttributeError):
            continue
        if not isinstance(message, str) or not isinstance(stamp, str) or not re.fullmatch(r"[0-9]{1,20}", stamp):
            continue
        for key in keys:
            match = COUNTS[key].search(message)
            if match:
                event = {"event": key, "unix_us": int(stamp)}
                if match.groups():
                    event["worker_pid"] = int(match[1])
                events.append(event)
    return {"readable": True, "lines_checked": min(len(lines), 10000),
            "truncated": len(lines) > 10000, "events": events}


def service_snapshot():
    raw = read_command("systemctl", "show", SERVICE, *[f"--property={key}" for key in FIELDS])
    result = {}
    for line in (raw or "").splitlines():
        key, _, value = line.partition("=")
        if key in FIELDS[:-1] and re.fullmatch(r"[0-9]{1,20}", value):
            result[key] = int(value)
        if key == "ActiveState" and value in {"active", "inactive", "activating", "deactivating", "failed"}:
            result[key] = value
    packages = sorted({
        match[1] for path in RELEASE_VENV.glob("lib/python*/site-packages/gunicorn-*.dist-info")
        if (match := re.fullmatch(r"gunicorn-([0-9]+\.[0-9]+\.[0-9]+)\.dist-info", path.name))
    })
    result["current_venv_gunicorn_versions"] = packages
    pid = result.get("MainPID", 0)
    try:
        result["runtime_mode"] = oct(stat.S_IMODE(RUNTIME.lstat().st_mode))
        result["control_socket_exists"] = stat.S_ISSOCK((RUNTIME / "gunicorn.ctl").lstat().st_mode)
    except OSError:
        pass
    if pid:
        try:
            proc = Path(f"/proc/{pid}")
            # Match only the expected value; never print any environment or argv.
            env = (proc / "environ").read_bytes().split(b"\0")
            result["runtime_env_matches"] = b"XDG_RUNTIME_DIR=/run/rastisi-gunicorn" in env
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            env_args = next((entry.partition(b"=")[2] for entry in env
                             if entry.startswith(b"GUNICORN_CMD_ARGS=")), b"").split()
            words = argv + env_args
            result["control_flags_visible"] = {
                flag.decode(): any(word.split(b"=", 1)[0] == flag for word in words)
                for flag in (b"--control-socket", b"--no-control-socket", b"--config")
            }
            uid = next(line.split()[2] for line in (proc / "status").read_text().splitlines()
                       if line.startswith("Uid:"))
            result["runtime_owner_matches_worker_uid"] = RUNTIME.lstat().st_uid == int(uid)
        except (OSError, StopIteration, ValueError):
            pass
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="ISO timestamp with timezone")
    parser.add_argument("--until", required=True, help="ISO timestamp with timezone")
    args = parser.parse_args()
    try:
        start, end = (datetime.fromisoformat(value) for value in (args.since, args.until))
        if start.utcoffset() is None or end.utcoffset() is None or not 0 < (end-start).total_seconds() <= 7200:
            raise ValueError
    except ValueError:
        parser.error("use two timezone-aware ISO timestamps spanning up to two hours")
    start, end = (value.strftime("%Y-%m-%d %H:%M:%S %z") for value in (start, end))
    tcp = read_command("ss", "-H", "-lnt", "sport", "=", ":18082")
    unix = read_command("ss", "-H", "-lx")
    print(json.dumps({
        "service": service_snapshot(),
        "tcp_18082_listening": None if tcp is None else bool(tcp.strip()),
        "legacy_socket_listening": None if unix is None else any(
            "/run/rasti.sock" in line.split() for line in unix.splitlines()),
        "gunicorn_window": journal(start, end, ("-u", SERVICE),
                                   ("worker_timeout", "worker_boot", "worker_kill", "control_permission")),
        "nginx_window": journal(start, end, ("-u", "nginx.service"),
                                ("nginx_upstream_timeout", "nginx_upstream_connect")),
        "kernel_window": journal(start, end, ("-k",), ("kernel_oom",)),
    }, indent=2))


if __name__ == "__main__":
    main()
