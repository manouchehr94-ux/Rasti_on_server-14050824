import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import availability_probe as probe


class AvailabilityTests(unittest.TestCase):
    def test_public_targets(self):
        targets = probe.routes("https://platform.example", "https://shop.platform.example/catalog/")
        self.assertEqual(set(targets), {"home", "login", "register", "tenant"})
        for url in ("http://example.com", "https://user@example.com", "https://example.com/?x=1",
                    "https://example.com:8080", "https://platform.example/shop"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.routes("https://platform.example", url)

    def test_probe_distinguishes_timeout_invalid_type_redirect_and_success(self):
        for stdout, code, healthy in (("000 ", 28, False), ("200 application/json", 0, False),
                                      ("302 text/html", 0, False), ("200 text/html; charset=utf-8", 0, True)):
            with self.subTest(stdout=stdout), patch.object(probe.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, stdout=stdout)) as run:
                self.assertEqual(probe.sample("https://platform.example", 8)["ok"], healthy)
                self.assertNotIn("--location", run.call_args.args[0])
                self.assertNotIn("--insecure", run.call_args.args[0])

    def test_two_consecutive_failures_dispatch_one_webhook_and_fail(self):
        unhealthy = {"status": 0, "ok": False, "ms": 8000}
        output = io.StringIO()
        with patch.object(probe, "sample", side_effect=[unhealthy, unhealthy]), \
                patch.object(probe.time, "sleep"), \
                patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="204")) as run:
            self.assertEqual(probe.monitor({"home": "https://platform.example"}, 60, 60, 8, output,
                                           "https://alerts.example/owner"), 1)
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["routes"], ["home"])
        self.assertEqual([json.loads(line)["status"] for line in output.getvalue().splitlines()], [0, 0])

    def test_success_resets_outage_streak(self):
        results = [{"status": status, "ok": status == 200, "ms": 10} for status in (503, 200, 503, 200)]
        with patch.object(probe, "sample", side_effect=results), patch.object(probe, "alert") as alert, \
                patch.object(probe.time, "monotonic", side_effect=[0, 0, 0, 20, 20, 40, 40, 60]), \
                patch.object(probe.time, "sleep"):
            self.assertEqual(probe.monitor({"home": "https://platform.example"}, 60, 20, 8, io.StringIO()), 0)
        alert.assert_not_called()

    def test_exact_soak_window_and_late_failure(self):
        class Clock:
            now = 0

            def monotonic(self):
                return self.now

            def sleep(self, delay):
                self.now += delay

        for late_failure in (False, True):
            with self.subTest(late_failure=late_failure):
                clock = Clock()
                observations = []

                def sample(_, timeout):
                    observations.append(clock.now)
                    healthy = not late_failure or clock.now < 3840
                    return {"ok": healthy, "status": 200 if healthy else 503, "ms": 50}

                with patch.object(probe.time, "monotonic", clock.monotonic), \
                        patch.object(probe.time, "sleep", clock.sleep), \
                        patch.object(probe, "sample", sample), patch.object(probe, "alert") as alert:
                    result = probe.monitor({"tenant": "https://tenant.example"}, 3900, 60, 8, io.StringIO())
                self.assertEqual(observations, list(range(0, 3901, 60)))
                self.assertEqual(result, int(late_failure))
                self.assertEqual(alert.call_count, int(late_failure))
