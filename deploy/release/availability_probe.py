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
    except subprocess.TimeoutExpired:
        status, healthy = 0, False
    return {"status": status, "ok": healthy, "ms": round((time.monotonic() - started) * 1000)}


def alert(failures, webhook):
    payload = {
        "message": "RastiSi external HTTPS check failed twice",
        "routes": sorted(failures),
        "observations": [sample for label in sorted(failures) for sample in failures[label]],
        "correlation": "compare UTC observations with WORKER TIMEOUT journal evidence",
    }
    print("::error::" + payload["message"] + ": " + ", ".join(payload["routes"]), file=sys.stderr)
    if webhook:
        # Do not follow webhook redirects or log transport errors containing its URL.
        try:
            result = subprocess.run(
                ["curl", "-q", "--silent", "--fail", "--proto", "=https", "--max-time", "10",
                 "--output", os.devnull, "--write-out", "%{http_code}", "--header", "Content-Type: application/json",
                 "--data-binary", "@-", "--", webhook],
                input=json.dumps(payload), text=True, capture_output=True, timeout=12,
            )
            delivered = result.returncode == 0 and result.stdout.startswith("2")
        except subprocess.TimeoutExpired:
            delivered = False
        if not delivered:
            print("::error::Availability webhook delivery failed", file=sys.stderr)


def monitor(targets, duration, interval, timeout, output, webhook=""):
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
        consecutive = {label: samples for label, samples in failures.items() if len(samples) >= 2}
        if consecutive:
            alert(consecutive, webhook)
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
    args = parser.parse_args()
    if min(args.duration, args.interval, args.timeout) <= 0:
        parser.error("timings must be positive")
    try:
        targets = routes(os.environ.get("RASTISI_PUBLIC_URL", ""), os.environ.get("RASTISI_TENANT_URL", ""))
    except ValueError as exc:
        parser.error(str(exc))
    if args.validate:
        return 0
    with open(args.output, "w", encoding="utf-8") as output:
        return monitor(targets, args.duration, args.interval, args.timeout, output,
                       os.environ.get("RASTISI_AVAILABILITY_WEBHOOK", ""))


if __name__ == "__main__":
    sys.exit(main())
