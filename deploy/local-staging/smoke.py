"""Run only inside the local staging network."""
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

opener = build_opener(ProxyHandler({}))
PATHS = ("/", "/login/", "/register/")


def read(origin, path):
    with opener.open(Request(origin + path, headers={"Host": "rastisi.localhost"}), timeout=6) as response:
        if response.status != 200 or response.url != origin + path:
            raise RuntimeError(f"unexpected status or redirect for {path}")
        return response.read(512), response.headers.get_content_type()


def smoke():
    for path in PATHS:
        _, mime = read("http://nginx:8080", path)
        if mime != "text/html":
            raise RuntimeError(f"unexpected content type for {path}")
    body, _ = read("http://nginx:8080", "/media/staging.txt")
    if body.strip() != b"Synthetic staging media":
        raise RuntimeError("synthetic public media missing")
    print("PASS homepage, login, register and synthetic public media")


def workers():
    master = int(Path("/tmp/gunicorn.pid").read_text())
    return set(map(int, Path(f"/proc/{master}/task/{master}/children").read_text().split()))


def worker():
    original = workers()
    if len(original) != 2:
        raise RuntimeError("expected two fixture workers")
    victim = min(original)
    os.kill(victim, signal.SIGSTOP)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = workers()
            if len(current) == 2 and victim not in current and current - original:
                read("http://127.0.0.1:8000", "/")
                print(f"PASS worker replaced: {victim}", flush=True)
                return
            time.sleep(0.25)
        raise RuntimeError("worker replacement not observed")
    finally:
        if victim in workers():
            os.kill(victim, signal.SIGCONT)


def logs():
    edge = {}
    app = {}
    for line in sys.stdin:
        offset = line.find("{")
        if offset < 0:
            continue
        try:
            row = json.loads(line[offset:])
        except json.JSONDecodeError:
            continue
        key = row.get("request_id")
        if not isinstance(key, str) or not re.fullmatch("[a-f0-9]{32}", key):
            continue
        if row.get("format") == "rastisi_availability_v1" and row.get("status") == "200":
            edge[key] = row
        if row.get("format") == "rastisi_worker_v1":
            app.setdefault(key, []).append(row)
    for key, events in app.items():
        if key not in edge or len(events) != 2:
            continue
        start, finish = sorted(events, key=lambda event: event["event"], reverse=True)
        if (start["event"] == "request_started" and finish["event"] == "request_finished"
                and start["pid"] == finish["pid"] and finish["duration_ms"] >= 0
                and edge[key]["upstream_addr"] != "-" and float(edge[key]["upstream_response_time"]) >= 0):
            print("PASS Nginx/Gunicorn request ID, PID, start and duration")
            return
    raise RuntimeError("no matching complete telemetry pair")


def soak():
    deadline = time.monotonic() + 3900
    streaks = dict.fromkeys(PATHS, 0)
    while True:
        start = time.monotonic()
        for path in PATHS:
            try:
                _, mime = read("http://nginx:8080", path)
                ok = mime == "text/html"
            except (OSError, RuntimeError):
                ok = False
            print(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "route": path, "ok": ok}), flush=True)
            streaks[path] = 0 if ok else streaks[path] + 1
        if max(streaks.values()) >= 2:
            raise SystemExit("FAIL two consecutive samples")
        if start >= deadline:
            return int(any(streaks.values()))
        time.sleep(max(0, min(start + 60, deadline) - time.monotonic()))


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "direct":
        read("http://127.0.0.1:8000", "/")
    else:
        sys.exit({"smoke": smoke, "worker": worker, "logs": logs, "soak": soak}[mode]())
