import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from apps.core import gunicorn_availability as hooks


class StageTwoTests(unittest.TestCase):
    def test_disabled_keeps_original_namespace(self):
        old = lambda worker: None
        namespace = {"worker_abort": old}
        with patch.dict(os.environ, {"RASTISI_AVAILABILITY_EVIDENCE": "0"}):
            hooks.install_hooks(namespace)
        self.assertEqual(namespace, {"worker_abort": old})

    def test_chains_all_existing_hooks_including_legacy_post_request(self):
        calls = []
        namespace = {
            "pre_request": lambda worker, req: calls.append("before"),
            "post_request": lambda worker, req: calls.append("after"),
            "worker_abort": lambda worker: calls.append("abort"),
            "post_worker_init": lambda worker: calls.append("ready"),
            "child_exit": lambda server, worker: calls.append("exit"),
        }
        with patch.dict(os.environ, {"RASTISI_AVAILABILITY_EVIDENCE": "1"}):
            hooks.install_hooks(namespace)
        stream = io.StringIO()
        logger = logging.Logger("stage2")
        logger.addHandler(logging.StreamHandler(stream))
        worker = SimpleNamespace(pid=12, log=logger)
        request = SimpleNamespace(headers=[("X-Request-ID", "a" * 32), ("Cookie", "PRIVATE")])
        namespace["pre_request"](worker, request)
        namespace["post_request"](worker, request, {"Authorization": "PRIVATE"},
                                  SimpleNamespace(status="503 Service Unavailable"))
        namespace["worker_abort"](worker)
        namespace["post_worker_init"](worker)
        namespace["child_exit"](SimpleNamespace(log=logger), worker)
        self.assertEqual(calls, ["before", "after", "abort", "ready", "exit"])
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([row["event"] for row in rows], [
            "request_started", "request_finished", "worker_abort", "worker_ready", "worker_exited",
        ])
        self.assertEqual(rows[1]["status"], 503)
        self.assertNotIn("PRIVATE", stream.getvalue())

    def test_gunicorn_validates_both_modes_with_unchanged_runtime_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "existing-gunicorn.conf.py"
            config.write_text(
                'bind = "127.0.0.1:18082"\nworkers = 1\nthreads = 2\ntimeout = 40\n'
                'def post_request(worker, req): pass\n'
                'from apps.core.gunicorn_availability import install_hooks\ninstall_hooks(globals())\n'
            )
            app = Path(tmp) / "fixture.py"
            app.write_text("def app(environ, start_response): pass\n")
            for flag in ("0", "1"):
                with self.subTest(flag=flag):
                    env = {**os.environ, "RASTISI_AVAILABILITY_EVIDENCE": flag,
                           "PYTHONPATH": os.pathsep.join([str(ROOT), tmp, os.getenv("PYTHONPATH", "")])}
                    env.pop("GUNICORN_CMD_ARGS", None)
                    result = subprocess.run(
                        [sys.executable, "-m", "gunicorn", "--print-config", "-c", str(config), "fixture:app"],
                        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
                    )
                    values = dict(tuple(part.strip() for part in line.split("=", 1))
                                  for line in result.stdout.splitlines() if "=" in line)
                    self.assertEqual([values[key] for key in ("bind", "workers", "threads", "timeout")],
                                     ["['127.0.0.1:18082']", "1", "2", "40"])

    def test_django_slow_sql_and_5xx_only_emit_sanitized_summary(self):
        code = """
import time
from types import SimpleNamespace
from django.conf import settings
settings.configure(DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}})
from django.db import connection
from apps.core.availability_evidence import AvailabilityEvidenceMiddleware, logger
connection.ensure_connection()
connection.connection.create_function('secret_query_name', 1, lambda _: time.sleep(0.26) or 1)
def handler(_):
    with connection.cursor() as cursor:
        cursor.execute('SELECT secret_query_name(%s)', ['SECRET_SQL_VALUE'])
    return SimpleNamespace(status_code=503)
logger.warning = lambda _, result: print(result)
AvailabilityEvidenceMiddleware(handler)(SimpleNamespace(META={'HTTP_X_REQUEST_ID': 'a' * 32}))
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, text=True, capture_output=True)
        row = json.loads(result.stdout)
        self.assertEqual((row["status"], row["slow_db_count"]), (503, 1))
        self.assertNotIn("SECRET_SQL_VALUE", result.stdout)
        self.assertNotIn("secret_query_name", result.stdout)
