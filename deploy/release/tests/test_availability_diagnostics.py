import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import availability_diagnostics as diagnostic


class ReadOnlyDiagnosticTests(unittest.TestCase):
    def test_allowlisted_event_fields_exclude_log_message_and_metadata(self):
        log = json.dumps({
            "__REALTIME_TIMESTAMP": "12345",
            "MESSAGE": "WORKER TIMEOUT (pid:42) Cookie=synthetic-private-value",
            "_CMDLINE": "postgres://sensitive",
        })
        with patch.object(diagnostic, "read_command", return_value=log) as run:
            result = diagnostic.journal("start", "end", ("-u", diagnostic.SERVICE), ("worker_timeout",))
        self.assertEqual(result["events"], [{"event": "worker_timeout", "unix_us": 12345, "worker_pid": 42}])
        self.assertEqual(run.call_args.args[0], "journalctl")
        self.assertNotIn("synthetic-private-value", json.dumps(result))

    def test_unreadable_journal_is_distinct_from_empty(self):
        with patch.object(diagnostic, "read_command", return_value=None):
            self.assertEqual(diagnostic.journal("start", "end", (), ()), {"readable": False})

    def test_logs_keep_counts_and_timings_without_customer_fields(self):
        from datetime import datetime
        import gzip
        from tempfile import TemporaryDirectory

        start = datetime.fromisoformat("2026-11-19T11:54:00+03:30")
        end = datetime.fromisoformat("2026-11-19T12:07:00+03:30")
        anchor = {"unix_us": int(diagnostic.INCIDENT_TIMEOUT.timestamp() * 1000000)}
        timed = json.dumps({
            "format": "rastisi_availability_v1", "at": "2026-11-19T11:55:55+03:30",
            "status": "504", "upstream_addr": "127.0.0.1:18082, unix:/run/rasti.sock",
            "upstream_response_time": "9.101, 0.200", "upstream_status": "504, 502",
            "request_id": "discard-this",
        })
        old = '198.51.100.77 - - [19/Nov/2026:12:06:00 +0330] "GET /customer?secret=private HTTP/1.1" 502 22'
        outside = old.replace("12:06:00", "12:08:00")
        with TemporaryDirectory() as temp:
            access = Path(temp, "access.log.2.gz")
            with gzip.open(access, "wt") as stream:
                stream.write("\n".join((timed, old, outside, "unrecognized")) + "\n")
            result = diagnostic.nginx_logs([access, access.with_suffix(".missing")], "access", start, end, [anchor])
            error = Path(temp, "error.log")
            error.write_text('2026/11/19 11:55:56 [error] x upstream timed out, client: 198.51.100.77, '
                             'request: "GET /secret", upstream: "http://unix:/run/rasti.sock:/private", host: "x"\n')
            errors = diagnostic.nginx_logs([error], "error", start, end, [anchor])
        self.assertEqual(result["files"][0]["in_window"], 2)
        self.assertEqual(result["files"][0]["unparsed"], 1)
        self.assertFalse(result["files"][1]["readable"])
        self.assertEqual(result["window_metrics"]["client_status.502"], 1)
        near = result["within_120s_of_worker_timeout"][0]["metrics"]
        self.assertNotIn("client_status.502", near)
        self.assertEqual(near["max_ms.release_port"], 9101)
        self.assertEqual(near["before.client_status.504"], 1)
        self.assertEqual(near["upstream_status.legacy_socket.502"], 1)
        self.assertEqual(errors["window_metrics"], {"nginx_upstream_timeout.legacy_socket": 1})
        for forbidden in ("discard-this", "/secret", "198.51.100.77", "/private"):
            self.assertNotIn(forbidden, json.dumps([result, errors]))

    def test_config_named_upstream_and_other_vhost_stay_distinct(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "nginx.conf"
            config.write_text("http { include sites/*.conf; }")
            (root / "sites").mkdir()
            (root / "sites/app.conf").write_text("""
                upstream app { server 127.0.0.1:18082; }
                server { server_name rastisi.ir; location / { proxy_pass http://app; } }
                server { server_name other.example; location / { proxy_pass http://unix:/run/rasti.sock:; } }
                server { server_name rastisi.ir; location /proxy { proxy_pass http://$destination; } }
            """)
            result = diagnostic.nginx_config(config, root, "rastisi.ir")
            (root / "sites/cycle.conf").write_text("include nginx.conf;")
            self.assertFalse(diagnostic.nginx_config(config, root, "rastisi.ir")["complete"])
        self.assertEqual(result["exact_server_name_candidates"],
                         {"named:release_port": 1, "dynamic_unresolved": 1})
        self.assertEqual(result["proxy_pass_candidates"]["legacy_socket"], 1)
        self.assertNotIn("other.example", json.dumps(result))

    def test_external_outage_correlation_excludes_unrelated_observations(self):
        from tempfile import TemporaryDirectory

        timeout = int(diagnostic.INCIDENT_TIMEOUT.timestamp() * 1_000_000)
        rows = [
            {"at": "2026-11-19T08:25:00+00:00", "route": "home", "ok": False},
            {"at": "2026-11-19T08:26:00+00:00", "route": "login", "ok": True},
            {"at": "2026-11-19T08:28:00+00:00", "route": "tenant", "ok": False},
            {"at": "2026-11-19T08:26:00+00:00", "route": "unknown-private-route", "ok": False},
        ]
        with TemporaryDirectory() as temp:
            path = Path(temp, "availability.jsonl")
            path.write_text("\n".join(map(json.dumps, rows)))
            report = diagnostic.external_correlation(path, [{"unix_us": timeout, "worker_pid": 42}])
        self.assertEqual(report["observations"], 3)
        self.assertEqual(report["within_120s_of_worker_timeout"][0]["external_counts"],
                         {"home.failed": 1, "login.ok": 1})
        self.assertNotIn("unknown-private-route", json.dumps(report))
