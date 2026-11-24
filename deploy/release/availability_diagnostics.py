#!/usr/bin/env python3
"""Print allowlisted, read-only incident evidence as JSON. Linux + systemd."""
import argparse
import contextlib
import glob
import gzip
import json
import os
import re
import stat
import subprocess
import zlib
from collections import Counter, defaultdict
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


# Nginx's default log escaping uses backslash escapes rather than literal
# quotes in request fields. The expressions take only fixed-position fields.
ACCESS = re.compile(r'^\S+ \S+ \S+ \[([^\]]+)\] "(?:[^"\\]|\\.)*" (\d{3})(?: |$)')
ERROR_TIME = re.compile(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[")
ERROR_UPSTREAM = re.compile(r', upstream: "(https?://[^"]+)"(?:, host:|, referrer:|$)')
CONFIG_TOKEN = re.compile(r'''\#[^\n]*|"[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*'|[{};]|[^\s{};#"'\\]+|\\.''')
INCIDENT_TIMEOUT = datetime.fromisoformat("2026-11-19T11:55:56+03:30")
MAX_LOG_BYTES = 64 * 1024 * 1024


def upstream_class(value):
    if value == "-" or not value:
        return "unrecorded"
    if re.match(r"^(?:https?://)?unix:/run/rasti\.sock(?::|$)", value):
        return "legacy_socket"
    if re.match(r"^(?:https?://)?(?:127\.0\.0\.1|\[::1\]):18082(?:/|$)", value):
        return "release_port"
    return "other"


def parse_log_line(line, kind, tz):
    if kind == "error":
        match = ERROR_TIME.match(line)
        if not match:
            return None
        at = datetime.strptime(match[1], "%Y/%m/%d %H:%M:%S").replace(tzinfo=tz)
        # Request and client fields may be present, but never reach the result.
        upstream = ERROR_UPSTREAM.search(line)
        prefix = line.split(", client:", 1)[0]
        event = next((key for key in ("nginx_upstream_timeout", "nginx_upstream_connect")
                      if COUNTS[key].search(prefix)), None)
        if event is None and "upstream prematurely closed connection" in prefix:
            event = "nginx_upstream_closed"
        return at.timestamp(), event, upstream_class(upstream[1] if upstream else ""), None, None
    if line.startswith("{"):
        entry = json.loads(line)
        if entry.get("format") != "rastisi_availability_v1":
            return None
        at = datetime.fromisoformat(entry["at"])
        if at.utcoffset() is None:
            return None
        status = entry["status"]
        if not isinstance(status, str) or not re.fullmatch(r"[1-5]\d{2}", status):
            return None
        # Nginx uses comma and colon separators for retries and redirects.
        addresses = re.split(r",\s*|\s+:\s+", entry["upstream_addr"])
        times = re.split(r",\s*|\s+:\s+", entry["upstream_response_time"])
        statuses = re.split(r",\s*|\s+:\s+", entry["upstream_status"])
        attempts = []
        for index, address in enumerate(addresses[:32]):
            raw_time = times[index] if index < len(times) else ""
            raw_status = statuses[index] if index < len(statuses) else ""
            elapsed = round(float(raw_time) * 1000) if re.fullmatch(r"\d{1,8}\.\d{3}", raw_time) else None
            code = int(raw_status) if re.fullmatch(r"[1-5]\d{2}", raw_status) else None
            attempts.append((upstream_class(address), elapsed, code))
        return at.timestamp(), "access", "recorded", int(status), attempts
    match = ACCESS.match(line)
    if match:
        at = datetime.strptime(match[1], "%d/%b/%Y:%H:%M:%S %z")
        return at.timestamp(), "access", "unrecorded", int(match[2]), None
    return None


def open_log(path):
    # Refuse special files and avoid blocking on a FIFO even if it replaces a
    # file between path selection and open. Production paths are operator-supplied.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    stream = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        stream.close()
        raise OSError("not a regular file")
    return stream


def nginx_logs(paths, kind, start, end, anchors):
    totals = Counter()
    correlated = [Counter() for _ in anchors]
    coverage = []
    for index, path in enumerate(paths):
        report = {"input": index + 1, "lines": 0, "unparsed": 0, "in_window": 0,
                  "readable": False, "limit_reached": False}
        coverage.append(report)
        try:
            with open_log(path) as raw, contextlib.ExitStack() as stack:
                stream = stack.enter_context(gzip.GzipFile(fileobj=raw)) if raw.peek(2)[:2] == b"\x1f\x8b" else raw
                report["readable"] = True
                remaining = MAX_LOG_BYTES
                while remaining > 0:
                    line = stream.readline(min(65537, remaining))
                    if not line:
                        break
                    remaining -= len(line)
                    if len(line) == 65537 and not line.endswith(b"\n"):
                        report["limit_reached"] = True
                        break
                    report["lines"] += 1
                    try:
                        result = parse_log_line(line.decode("utf-8", "replace"), kind, start.tzinfo)
                    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                        result = None
                    if result is None:
                        report["unparsed"] += 1
                        continue
                    at, event, route, status, attempts = result
                    report["first_unix_s"] = min(int(at), report.get("first_unix_s", int(at)))
                    report["last_unix_s"] = max(int(at), report.get("last_unix_s", int(at)))
                    if not start.timestamp() <= at <= end.timestamp():
                        continue
                    report["in_window"] += 1
                    if event is None:
                        continue
                    metrics = Counter({f"{event}.{route}": 1})
                    if status is not None:
                        metrics[f"client_status.{status}"] += 1
                    for label, elapsed, code in attempts or ():
                        metrics[f"upstream_attempt.{label}"] += 1
                        if code is not None:
                            metrics[f"upstream_status.{label}.{code}"] += 1
                        if elapsed is not None:
                            metrics[f"timed_attempt.{label}"] += 1
                            metrics[f"total_ms.{label}"] += elapsed
                            totals[f"max_ms.{label}"] = max(totals[f"max_ms.{label}"], elapsed)
                    totals.update(metrics)
                    for anchor, bucket in zip(anchors, correlated):
                        delta = at - anchor["unix_us"] / 1_000_000
                        if abs(delta) <= 120:
                            bucket.update(metrics)
                            if kind == "error" or status in (499, 502, 504):
                                phase = "before" if delta < 0 else "at_or_after"
                                signal = f"client_status.{status}" if kind == "access" else f"{event}.{route}"
                                bucket[f"{phase}.{signal}"] += 1
                            for label, elapsed, _ in attempts or ():
                                if elapsed is not None:
                                    bucket[f"max_ms.{label}"] = max(bucket[f"max_ms.{label}"], elapsed)
                report["limit_reached"] |= remaining == 0
        except (OSError, EOFError, zlib.error):
            report["read_error"] = True
    return {"files": coverage, "window_metrics": dict(totals),
            "within_120s_of_worker_timeout": [
                {**anchor, "metrics": dict(bucket)} for anchor, bucket in zip(anchors, correlated)
            ]}


def config_directives(path, prefix, seen, scopes=(), chain=()):
    """Walk a bounded subset of the config grammar without invoking Nginx."""
    canonical = path.resolve()
    if canonical in chain or len(chain) >= 16 or len(seen) >= 128:
        raise ValueError("config walk limit")
    seen.add(canonical)
    with open_log(path) as stream:
        content = stream.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise ValueError("config file limit")
    inherited_depth = len(scopes)
    args = []
    for token in CONFIG_TOKEN.findall(content.decode("utf-8", "replace")):
        if token.startswith("#"):
            continue
        if token == "{":
            if not args:
                raise ValueError("unexpected block")
            scopes += ((tuple(args), object()),)
            args = []
        elif token == "}":
            if len(scopes) <= inherited_depth or args:
                raise ValueError("unexpected block end")
            scopes = scopes[:-1]
        elif token == ";":
            if not args:
                continue
            if args[0] == "include" and len(args) == 2:
                matches = sorted(glob.iglob(str(prefix / args[1])))
                if not matches and not glob.has_magic(args[1]):
                    raise OSError("explicit include missing")
                for included in matches:
                    yield from config_directives(Path(included), prefix, seen, scopes, (*chain, canonical))
            else:
                yield scopes, tuple(args)
            args = []
        else:
            args.append(token.strip("\"'"))
    if args or len(scopes) != inherited_depth:
        raise ValueError("incomplete directive or block")


def nginx_config(path, prefix, host):
    seen = set()
    groups = defaultdict(set)
    proxies = []
    names = defaultdict(set)
    try:
        for scopes, args in config_directives(path, prefix, seen):
            if len(args) < 2:
                continue
            group = next((header[1] for header, _ in scopes
                          if len(header) == 2 and header[0] == "upstream"), None)
            server = next((identity for header, identity in reversed(scopes)
                           if header[0] == "server"), None)
            if server is not None and args[0] == "server_name":
                names[server].update(name.lower() for name in args[1:])
            if group and args[0] == "server":
                groups[group].add(upstream_class(args[1]))
            if args[0] == "proxy_pass":
                proxies.append((server, args[1]))
    except (OSError, ValueError, RuntimeError):
        return {"complete": False, "files_seen": len(seen)}
    routes = Counter()
    host_routes = Counter()
    for server, value in proxies:
        destination = re.match(r"^https?://([^/]+)", value)
        if "$" in value:
            label = "dynamic_unresolved"
        elif destination and destination[1] in groups:
            label = "named:" + "+".join(sorted(groups[destination[1]]))
        else:
            label = upstream_class(value)
        routes[label] += 1
        if host in names[server]:
            host_routes[label] += 1
    return {"complete": True, "files_seen": len(seen), "proxy_pass_candidates": dict(routes),
            "exact_server_name_candidates": dict(host_routes)}


def external_correlation(path, anchors):
    """Correlate sanitized external artifacts without contacting either host."""
    summaries = [Counter() for _ in anchors]
    observations = 0
    readable = False
    truncated = False
    try:
        with open_log(path) as stream:
            readable = True
            for _ in range(10000):
                line = stream.readline(4097)
                if not line:
                    break
                if len(line) == 4097:
                    truncated = True
                    break
                try:
                    row = json.loads(line)
                    route = row["route"]
                    healthy = row["ok"]
                    stamp = datetime.fromisoformat(row["at"])
                    if (route not in ("home", "login", "register", "tenant")
                            or type(healthy) is not bool or stamp.utcoffset() is None):
                        continue
                    instant = stamp.timestamp()
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
                observations += 1
                for anchor, counts in zip(anchors, summaries):
                    if abs(instant - anchor["unix_us"] / 1_000_000) <= 120:
                        counts[f"{route}.{'ok' if healthy else 'failed'}"] += 1
            else:
                truncated = True
    except OSError:
        readable = False
    return {
        "readable": readable, "limit_reached": truncated, "observations": observations,
        "within_120s_of_worker_timeout": [
            {**anchor, "external_counts": dict(counts)} for anchor, counts in zip(anchors, summaries)
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="ISO timestamp with timezone")
    parser.add_argument("--until", required=True, help="ISO timestamp with timezone")
    parser.add_argument("--nginx-access", type=Path, action="append", default=[],
                        help="repeat for each access log, including rotated .gz")
    parser.add_argument("--nginx-error", type=Path, action="append", default=[],
                        help="repeat for each error log, including rotated .gz")
    parser.add_argument("--nginx-config", type=Path, default=Path("/etc/nginx/nginx.conf"))
    parser.add_argument("--nginx-host", default="rastisi.ir", help="exact server_name to inspect")
    parser.add_argument("--nginx-prefix", type=Path, default=Path("/etc/nginx"))
    parser.add_argument("--external-observations", type=Path,
                        help="optional sanitized availability.jsonl from the external monitor")
    args = parser.parse_args()
    if len(args.nginx_access) + len(args.nginx_error) > 16:
        parser.error("provide at most 16 log files")
    try:
        start, end = (datetime.fromisoformat(value) for value in (args.since, args.until))
        if start.utcoffset() is None or end.utcoffset() is None or not 0 < (end-start).total_seconds() <= 7200:
            raise ValueError
    except ValueError:
        parser.error("use two timezone-aware ISO timestamps spanning up to two hours")
    start_arg, end_arg = (value.strftime("%Y-%m-%d %H:%M:%S %z") for value in (start, end))
    tcp = read_command("ss", "-H", "-lnt", "sport", "=", ":18082")
    unix = read_command("ss", "-H", "-lx")
    gunicorn = journal(start_arg, end_arg, ("-u", SERVICE),
                       ("worker_timeout", "worker_boot", "worker_kill", "control_permission"))
    anchors = [event for event in gunicorn.get("events", []) if event["event"] == "worker_timeout"]
    anchors_truncated = len(anchors) > 64
    anchors = anchors[:64]
    if not anchors and start <= INCIDENT_TIMEOUT <= end:
        anchors = [{"event": "reported_worker_timeout", "worker_pid": 2575151,
                    "unix_us": int(INCIDENT_TIMEOUT.timestamp() * 1_000_000)}]
    access = args.nginx_access or sorted(Path("/var/log/nginx").glob("access.log*"))[:8]
    errors = args.nginx_error or sorted(Path("/var/log/nginx").glob("error.log*"))[:8]
    print(json.dumps({
        "external_correlation": (external_correlation(args.external_observations, anchors)
                                 if args.external_observations else None),
        "worker_timeout_anchors_truncated": anchors_truncated,
        "nginx_access_files": nginx_logs(access, "access", start, end, anchors),
        "nginx_error_files": nginx_logs(errors, "error", start, end, anchors),
        "nginx_config_candidates": nginx_config(args.nginx_config, args.nginx_prefix, args.nginx_host.lower()),
        "service": service_snapshot(),
        "tcp_18082_listening": None if tcp is None else bool(tcp.strip()),
        "legacy_socket_listening": None if unix is None else any(
            "/run/rasti.sock" in line.split() for line in unix.splitlines()),
        "gunicorn_window": gunicorn,
        "nginx_window": journal(start_arg, end_arg, ("-u", "nginx.service"),
                                ("nginx_upstream_timeout", "nginx_upstream_connect")),
        "kernel_window": journal(start_arg, end_arg, ("-k",), ("kernel_oom",)),
    }, indent=2))


if __name__ == "__main__":
    main()
