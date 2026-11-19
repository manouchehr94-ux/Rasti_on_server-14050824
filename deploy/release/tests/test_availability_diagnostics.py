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
