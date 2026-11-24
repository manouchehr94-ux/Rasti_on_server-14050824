#!/usr/bin/env python3
"""Join allowlisted edge, worker, app and resource evidence for a UTC incident window."""
import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from availability_diagnostics import SERVICE, journal, open_log, parse_log_line, read_command

RID = re.compile(r"[a-f0-9]{32}")
WORKER_EVENTS = {"request_started", "request_finished", "worker_abort", "worker_ready", "worker_exited"}
RESOURCE_KEYS = set("""unix_ms cpu user nice system idle iowait irq softirq steal ram_kib
MemAvailable SwapFree cgroups_service_to_root memory_bytes memory.current memory.max memory.high
oom_local oom oom_kill high max usage_usec nr_throttled throttled_usec postgres clients_including_probe
active_others lock_waiters active_over_1s max_connections redis used_memory maxmemory
connected_clients blocked_clients rejected_connections evicted_keys instantaneous_ops_per_sec""".split())
READ_LIMIT = 16 * 1024 * 1024
REPORT_REQUEST_LIMIT = 200


def numeric_tree(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, dict):
        return {key: numeric_tree(item, depth + 1) for key, item in value.items() if key in RESOURCE_KEYS}
    if isinstance(value, list):
        return [numeric_tree(item, depth + 1) for item in value[:16]]
    return value if type(value) is int and 0 <= value < 10**20 or value == "max" else None


def object_line(line):
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def file_rows(paths, coverage):
    for index, path in enumerate(paths):
        state = {"file": index, "readable": False, "tail_only": False}
        coverage.append(state)
        try:
            with open_log(path) as stream:
                size = stream.seek(0, 2)
                state.update(readable=True, tail_only=size > READ_LIMIT)
                stream.seek(max(0, size - READ_LIMIT))
                if state["tail_only"]:
                    stream.readline(8192)
                for line in stream.read(READ_LIMIT).splitlines():
                    if len(line) <= 8192:
                        yield line
        except OSError:
            continue


def request_event(record):
    kind = record.get("format")
    event = record.get("event")
    seconds = record.get("unix_ms")
    if type(seconds) is not int or not 0 <= seconds < 10**15 or not isinstance(event, str):
        return None
    if not ((kind == "rastisi_worker_v1" and event in WORKER_EVENTS)
            or (kind == "rastisi_app_v1" and event == "request_summary")):
        return None
    fields = {name: value for name in ("unix_ms", "pid", "status", "duration_ms", "slow_db_count",
                                      "slow_db_total_ms", "slow_db_max_ms")
              if type(value := record.get(name)) is int and 0 <= value < 10**15}
    rid = record.get("request_id")
    if isinstance(rid, str) and RID.fullmatch(rid):
        fields["request_id"] = rid
    elif event in {"request_started", "request_finished", "request_summary"}:
        return None
    return {"event": event, **fields}


def edge_event(line, entry, end):
    rid = entry.get("request_id")
    if not isinstance(rid, str) or not RID.fullmatch(rid):
        return None
    try:
        edge = parse_log_line(line.decode("utf-8"), "access", end.tzinfo)
        if edge is None:
            return None
        at, _, _, status, attempts = edge
        epoch = entry.get("end_epoch")
        if isinstance(epoch, str) and re.fullmatch(r"[0-9]{1,12}\.[0-9]{3}", epoch):
            at = float(epoch)
        value = entry.get("request_time")
        elapsed = round(float(value) * 1000) if isinstance(value, str) and re.fullmatch(r"[0-9]{1,8}\.[0-9]{3}", value) else None
    except (ValueError, AttributeError, KeyError, TypeError):
        return None
    end_ms = round(at * 1000)
    return {"request_id": rid, "event": "edge_completed", "unix_ms": end_ms, "status": status,
            "start_ms": end_ms - elapsed if elapsed is not None else None, "duration_ms": elapsed,
            "upstream_attempts": [{"destination": dest, "ms": ms, "status": code}
                                  for dest, ms, code in attempts or ()]}


def incident(start, end, edge_paths, resource_paths, external_paths=()):
    low, high = (round(value.timestamp() * 1000) for value in (start, end))
    first, last = (value.strftime("%Y-%m-%d %H:%M:%S %z") for value in (start, end))
    log = read_command("journalctl", "--no-pager", "-o", "cat", "-u", SERVICE,
                       "--since", first, "--until", last, "-n", "10001")
    edges, resources, lifecycle = [], [], []
    requests = defaultdict(list)
    coverage = {"edge": [], "resources": [], "external": [], "journal_readable": log is not None,
                "journal_limit_reached": bool(log and log.count("\n") >= 10000)}
    for line in (log or "").splitlines()[-10000:]:
        match = re.search(r'\{"format":"rastisi_(?:worker|app)_v1",', line)
        event = request_event(object_line(line[match.start():])) if match else None
        if event and low <= event["unix_ms"] <= high:
            if "request_id" in event:
                requests[event["request_id"]].append(event)
            else:
                lifecycle.append(event)
    for line in file_rows(edge_paths, coverage["edge"]):
        entry = object_line(line)
        if entry.get("format") != "rastisi_availability_v1":
            continue
        event = edge_event(line, entry, end)
        if event and low <= event["unix_ms"] <= high:
            edges.append(event)
    for event in edges:
        requests[event["request_id"]].append(event)
    for line in file_rows(resource_paths, coverage["resources"]):
        entry = object_line(line)
        stamp = entry.get("unix_ms")
        if entry.get("format") == "rastisi_resources_v1" and type(stamp) is int and low <= stamp <= high:
            resources.append(numeric_tree(entry))
    external = []
    for line in file_rows(external_paths, coverage["external"]):
        entry = object_line(line)
        route = entry.get("route")
        status = entry.get("status")
        duration = entry.get("ms")
        try:
            stamp = datetime.fromisoformat(entry.get("at"))
            if stamp.utcoffset() is None:
                continue
            unix_ms = round(stamp.timestamp() * 1000)
        except (TypeError, ValueError, OverflowError):
            continue
        if (low <= unix_ms <= high and route in ("home", "login", "register", "tenant")
                and type(status) is int and 0 <= status <= 599 and type(entry.get("ok")) is bool
                and type(duration) is int and 0 <= duration <= 120_000):
            external.append({"unix_ms": unix_ms, "route": route, "status": status,
                             "ok": entry["ok"], "duration_ms": duration})
    relevant = []
    for rid, events in requests.items():
        types = {event["event"] for event in events}
        unmatched = "request_started" in types and "request_finished" not in types
        if unmatched or any(event.get("status", 0) >= 499 or (event.get("duration_ms") or 0) >= 1000
                            or event["event"] == "request_summary" for event in events):
            relevant.append({"request_id": rid, "worker_finish_missing": unmatched,
                             "events": sorted(events, key=lambda event: event["unix_ms"])})
    relevant.sort(key=lambda item: item["events"][0]["unix_ms"])
    resources.sort(key=lambda item: item["unix_ms"])
    external.sort(key=lambda item: item["unix_ms"])
    return {"service": SERVICE, "window": [start.isoformat(), end.isoformat()], "coverage": coverage,
            "worker_lifecycle": lifecycle[-200:],
            "worker_journal": journal(first, last, ("-u", SERVICE), ("worker_timeout", "worker_kill", "worker_boot")),
            "kernel_oom": journal(first, last, ("-k",), ("kernel_oom",)),
            "requests_matching_incident_criteria": len(relevant), "requests": relevant[-REPORT_REQUEST_LIMIT:],
            "resource_samples": resources[-120:], "external_samples": external[-500:],
            "unknowns": ["missing finish can mean a hung worker or an incomplete log window",
                         "database wait counters are instance-wide and do not identify a request",
                         "timestamp proximity does not prove causality"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--edge", type=Path, action="append", default=[])
    parser.add_argument("--resources", type=Path, action="append", default=[])
    parser.add_argument("--external", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        start, end = map(datetime.fromisoformat, (args.since, args.until))
        if (start.utcoffset() is None or end.utcoffset() is None
                or not 0 < (end - start).total_seconds() <= 7200 or len(args.edge) + len(args.resources) + len(args.external) > 4):
            raise ValueError
    except ValueError:
        parser.error("use an explicit timezone, a window of up to two hours and up to four files")
    print(json.dumps(incident(start, end, args.edge, args.resources, args.external), indent=2))


if __name__ == "__main__":
    main()
