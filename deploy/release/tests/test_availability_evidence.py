import concurrent.futures
import importlib.util
import io
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))
import availability_incident as incident
import availability_probe as probe
import availability_resources as resources


class EvidenceTests(unittest.TestCase):
    def test_incident_omits_raw_fields_and_marks_unfinished_requests(self):
        now = datetime.now(timezone.utc)
        stamp = int(now.timestamp() * 1000)
        rid = "a" * 32
        start = {"format": "rastisi_worker_v1", "event": "request_started", "unix_ms": stamp,
                 "request_id": rid, "pid": 1, "authorization": "PRIVATE_MARKER"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "edge"
            path.write_text(json.dumps({"format": "rastisi_availability_v1", "at": now.isoformat(),
                                        "status": "504", "request_id": rid, "request_time": "1.000",
                                        "upstream_addr": "127.0.0.1:18082",
                                        "upstream_response_time": "1.000", "upstream_status": "504",
                                        "cookie": "PRIVATE_MARKER"}))
            with patch.object(incident, "read_command", return_value=json.dumps(start, separators=(",", ":"))), \
                    patch.object(incident, "journal", return_value={"events": []}):
                report = incident.incident(now - timedelta(seconds=3), now + timedelta(seconds=3), [path], [])
        request = report["requests"][0]
        self.assertTrue(request["worker_finish_missing"])
        self.assertEqual({event["event"] for event in request["events"]}, {"request_started", "edge_completed"})
        self.assertNotIn("PRIVATE_MARKER", json.dumps(report))

    def test_resource_aggregates_exclude_redis_keys_and_postgres_details(self):
        with patch.dict(os.environ, {"RASTISI_EVIDENCE_PGSERVICE": "fixture",
                                     "RASTISI_EVIDENCE_REDIS_SOCKET": "/tmp/fixture.sock"}), \
                patch.object(resources, "command", side_effect=["4|3|2|1|30", "blocked_clients:2\r\nunknown:PRIVATE"]):
            self.assertEqual(resources.postgres()["lock_waiters"], 2)
            self.assertEqual(resources.redis(), {"blocked_clients": 2})


    @unittest.skipUnless(importlib.util.find_spec("django"), "Django is required for SQL timing")
    def test_django_times_real_sql_without_logging_sql_or_parameters(self):
        code = """
import json
import time
from types import SimpleNamespace
from django.conf import settings
settings.configure(DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}})
from django.db import connection
from apps.core.availability_evidence import AvailabilityEvidenceMiddleware, logger
connection.ensure_connection()
connection.connection.create_function('fixture_slow', 1, lambda _: time.sleep(0.27) or 1)
def handler(_):
    with connection.cursor() as cursor:
        cursor.execute('SELECT fixture_slow(%s)', ['PRIVATE_SQL_PARAMETER'])
    return SimpleNamespace(status_code=503)
def capture(_, result):
    print(result)
logger.warning = capture
AvailabilityEvidenceMiddleware(handler)(SimpleNamespace(META={'HTTP_X_REQUEST_ID': 'a' * 32}))
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=RELEASE.parents[1],
                                check=True, text=True, capture_output=True)
        event = json.loads(result.stdout)
        self.assertEqual((event["slow_db_count"], event["status"]), (1, 503))
        self.assertGreaterEqual(event["slow_db_max_ms"], 250)
        self.assertNotIn("fixture_slow", result.stdout + result.stderr)
        self.assertNotIn("PRIVATE_SQL_PARAMETER", result.stdout + result.stderr)


def wait(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(0.05)
    raise AssertionError("fixture deadline exceeded")


def port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


@unittest.skipUnless(os.environ.get("RASTISI_TEST_NGINX"), "requires explicit path to a local nginx binary")
class IsolatedAvailabilityTest(unittest.TestCase):
    def test_worker_stall_tls_failure_and_webhook_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fixture = self.fixture(base)
            with fixture["worker_log"].open("w") as log:
                gunicorn = subprocess.Popen(
                    [sys.executable, "-m", "gunicorn", "-c", str(RELEASE / "examples/gunicorn-availability.py"),
                     "-b", f"127.0.0.1:{fixture['backend']}", "-k", "gthread", "--threads", "2", "-t", "3",
                     "--no-control-socket", "fixture:app"], cwd=base, stdout=log, stderr=log,
                )
                self.addCleanup(self.stop, gunicorn)
            nginx = subprocess.Popen([os.environ["RASTISI_TEST_NGINX"], "-p", str(base),
                                      "-c", str(base / "nginx.conf"), "-g", "daemon off;"])
            self.addCleanup(self.stop, nginx)
            with patch.dict(os.environ, {"CURL_CA_BUNDLE": str(base / "cert.pem"),
                                         "NO_PROXY": "localhost,127.0.0.1"}):
                origin = f"https://localhost:{fixture['edge']}"
                wait(lambda: probe.sample(origin, 2)["ok"])
                subprocess.run(
                    ["curl", "-q", "--silent", "--fail", "--max-time", "2",
                     "-H", "Cookie: PRIVATE_MARKER", "-H", "Authorization: PRIVATE_MARKER",
                     "-H", "X-Request-ID: " + "f" * 32,
                     origin + "/slow?payment=PRIVATE_MARKER"], check=True, capture_output=True,
                )
                edge = [json.loads(line) for line in fixture["edge_log"].read_text().splitlines()][-1]
                records = self.records(fixture["worker_log"])
                matching = [row for row in records if row.get("request_id") == edge["request_id"]]
                self.assertEqual([row["event"] for row in matching], ["request_started", "request_finished"])
                self.assertEqual(matching[-1]["status"], 200)
                self.assertGreaterEqual(matching[-1]["duration_ms"], 1000)
                self.assertNotEqual(edge["request_id"], "f" * 32)
                self.assertEqual(edge["upstream_addr"], f"127.0.0.1:{fixture['backend']}")
                pid = matching[-1]["pid"]
                before_stall = {row.get("request_id") for row in records}
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    stalled = pool.submit(probe.sample, origin + "/stall", 3)
                    started = wait(lambda: next((row for row in self.records(fixture["worker_log"])
                                                  if row["event"] == "request_started"
                                                  and row["request_id"] not in before_stall), None))
                    os.kill(pid, signal.SIGSTOP)
                    self.assertEqual(stalled.result()["status"], 504)
                failed_edge = json.loads(fixture["edge_log"].read_text().splitlines()[-1])
                self.assertEqual(failed_edge["request_id"], started["request_id"])
                self.assertFalse(any(row.get("request_id") == started["request_id"]
                                     and row["event"] == "request_finished"
                                     for row in self.records(fixture["worker_log"])))
                wait(lambda: "WORKER TIMEOUT" in fixture["worker_log"].read_text())
                wait(lambda: any(row["event"] == "worker_ready" and row["pid"] != pid
                                 for row in self.records(fixture["worker_log"])))
                self.assertTrue(any(row["event"] == "worker_exited" and row["pid"] == pid
                                    for row in self.records(fixture["worker_log"])))
                wait(lambda: probe.sample(origin, 3)["ok"])

                sink, received = self.receiver(base)
                webhook = f"https://localhost:{sink.server_port}/alert"
                wrong_host = f"https://127.0.0.1:{fixture['edge']}/"
                self.assertEqual(probe.sample(wrong_host, 2)["reason"], "tls")
                report = io.StringIO()
                self.assertEqual(probe.monitor({"home": wrong_host}, 1, 1, 2, report, webhook,
                                               webhook.replace("/alert", "/heartbeat")), 1)
                self.assertEqual([path for path, _ in received], ["/heartbeat", "/heartbeat", "/alert"])
                self.assertEqual(len(received[-1][1]["observations"]), 2)
                self.assertNotIn("PRIVATE_MARKER", fixture["edge_log"].read_text()
                                 + fixture["worker_log"].read_text() + report.getvalue())

    def fixture(self, base):
        edge, backend = port(), port()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                        "-keyout", str(base / "key.pem"), "-out", str(base / "cert.pem")],
                       check=True, capture_output=True)
        (base / "fixture.py").write_text("""import time
def app(env, start_response):
    if env["PATH_INFO"] == "/slow":
        time.sleep(1.1)
    if env["PATH_INFO"] == "/stall":
        time.sleep(60)
    start_response("200 OK", [("Content-Type", "text/html")])
    return [b"fixture"]
""")
        edge_log, worker_log = base / "edge.jsonl", base / "worker.log"
        (base / "nginx.conf").write_text(f"""pid {base}/nginx.pid;
error_log {base}/nginx-error.log;
events {{}}
http {{
    client_body_temp_path {base}/body;
    proxy_temp_path {base}/proxy;
    fastcgi_temp_path {base}/fastcgi;
    uwsgi_temp_path {base}/uwsgi;
    scgi_temp_path {base}/scgi;
    include {RELEASE}/examples/nginx-availability-http.conf;
    server {{
        listen 127.0.0.1:{edge} ssl;
        ssl_certificate {base}/cert.pem;
        ssl_certificate_key {base}/key.pem;
        access_log {edge_log} rastisi_availability;
        location / {{
            proxy_set_header X-Request-ID $request_id;
            proxy_read_timeout 1500ms;
            proxy_pass http://127.0.0.1:{backend};
        }}
    }}
}}
""")
        return {"edge": edge, "backend": backend, "edge_log": edge_log, "worker_log": worker_log}

    def receiver(self, base):
        received = []

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = HTTPServer(("127.0.0.1", 0), Receiver)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(base / "cert.pem", base / "key.pem")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, received

    @staticmethod
    def records(path):
        return [json.loads(line[line.index('{"format"'):]) for line in path.read_text().splitlines()
                if '{"format"' in line]

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    unittest.main()
