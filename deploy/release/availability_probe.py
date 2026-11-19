#!/usr/bin/env python3
"""External HTTPS health sampling. Records only labels, statuses and timings."""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit


def routes(origin, tenant):
    for url in (origin, tenant):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.query or parsed.fragment or any(c.isspace() for c in url)):
            raise ValueError("Configure plain HTTPS URLs without credentials, query or fragment")
        if parsed.port not in (None, 443):
            raise ValueError("Public probes require the standard HTTPS port")
    if urlsplit(origin).path not in ("", "/"):
        raise ValueError("The platform URL must be an origin")
    if urlsplit(origin).hostname == urlsplit(tenant).hostname:
        raise ValueError("Configure a distinct representative tenant hostname")
    origin = origin.rstrip("/")
    return {"home": origin + "/", "login": origin + "/login/",
            "register": origin + "/register/", "tenant": tenant}


def sample(url, timeout):
    started = time.monotonic()
    try:
        result = subprocess.run(
            ["curl", "-q", "--silent", "--proto", "=https", "--max-time", str(timeout),
             "--output", os.devnull, "--write-out", "%{http_code} %{content_type}", "--", url],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        code, _, mime = result.stdout.partition(" ")
        status = int(code) if code.isdecimal() else 0
        healthy = result.returncode == 0 and status == 200 and mime.lower().startswith("text/html")
        reason = {6: "dns", 7: "connect", 28: "timeout", 35: "tls", 60: "tls"}.get(result.returncode)
        if not healthy and reason is None:
            reason = "transport" if result.returncode else "http_or_content"
    except subprocess.TimeoutExpired:
        status, healthy, reason = 0, False, "timeout"
    except OSError:
        status, healthy, reason = 0, False, "probe_unavailable"
    return {"status": status, "ok": healthy,
            "ms": round((time.monotonic() - started) * 1000), "reason": reason}


def valid_notification_url(url):
    try:
        parsed = urlsplit(url)
        return bool(parsed.scheme == "https" and parsed.hostname and parsed.username is None
                    and not parsed.fragment and all(33 <= ord(char) <= 126 for char in url))
    except ValueError:
        return False


def post(url, payload):
    # curl config on stdin keeps the URL and payload out of process arguments.
    if not valid_notification_url(url):
        return False
    try:
        config = f"url = {json.dumps(url)}\ndata-binary = {json.dumps(json.dumps(payload))}\n"
        result = subprocess.run(
            ["curl", "-q", "--silent", "--fail", "--proto", "=https", "--max-time", "10",
             "--output", os.devnull, "--write-out", "%{http_code}", "--header",
             "Content-Type: application/json", "--config", "-"],
            input=config, text=True, capture_output=True, timeout=12,
        )
        return result.returncode == 0 and result.stdout.startswith("2")
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return False


def alert(failures, webhook):
    payload = {
        "message": "RastiSi external HTTPS check failed twice",
        "routes": sorted(failures),
        "observations": [sample for label in sorted(failures) for sample in failures[label]],
        "correlation": "compare UTC observations with WORKER TIMEOUT journal evidence",
    }
    print("::error::" + payload["message"] + ": " + ", ".join(payload["routes"]), file=sys.stderr)
    if webhook and not post(webhook, payload):
        print("::error::Availability webhook delivery failed", file=sys.stderr)


def monitor(targets, duration, interval, timeout, output, webhook="", heartbeat=""):
    failures = {label: [] for label in targets}
    deadline = time.monotonic() + duration
    while True:
        started = time.monotonic()
        for label, url in targets.items():
            at = datetime.now(timezone.utc).isoformat()
            result = sample(url, timeout)
            observation = {"at": at, "route": label, **result}
            print(json.dumps(observation), file=output, flush=True)
            if result["ok"]:
                failures[label].clear()
            else:
                failures[label].append(observation)
        heartbeat_failed = heartbeat and not post(heartbeat, {"event": "probe_round_completed"})
        if heartbeat_failed:
            print("::error::Availability heartbeat delivery failed", file=sys.stderr)
        consecutive = {label: samples for label, samples in failures.items() if len(samples) >= 2}
        if consecutive:
            alert(consecutive, webhook)
            return 1
        if heartbeat_failed:
            return 1
        if started >= deadline:
            return int(any(failures.values()))
        time.sleep(max(0, min(started + interval, deadline) - time.monotonic()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--output", default="availability.jsonl")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--require-notifications", action="store_true")
    args = parser.parse_args()
    if min(args.duration, args.interval, args.timeout) <= 0:
        parser.error("timings must be positive")
    try:
        targets = routes(os.environ.get("RASTISI_PUBLIC_URL", ""), os.environ.get("RASTISI_TENANT_URL", ""))
    except ValueError as exc:
        parser.error(str(exc))
    webhook = os.environ.get("RASTISI_AVAILABILITY_WEBHOOK", "")
    heartbeat = os.environ.get("RASTISI_AVAILABILITY_HEARTBEAT", "")
    if args.require_notifications and (webhook == heartbeat or not all(map(valid_notification_url, (webhook, heartbeat)))):
        parser.error("configure separate HTTPS outage and heartbeat endpoints")
    if args.validate:
        return 0
    with open(args.output, "w", encoding="utf-8") as output:
        return monitor(targets, args.duration, args.interval, args.timeout, output, webhook, heartbeat)


if __name__ == "__main__":
    sys.exit(main())
