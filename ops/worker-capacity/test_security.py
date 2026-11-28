import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
SQL = (ROOT / "postgres-capacity.sql").read_bytes()


class ProbeSecurity(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.bin = Path(temporary.name)
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}

    def stub(self, name, code):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + code)
        path.chmod(0o700)

    def postgres(self, sql):
        return subprocess.run(
            ["sh", str(ROOT / "postgres-capacity.sh")], input=sql,
            env=self.env, capture_output=True,
        )

    def test_host_output_drops_unapproved_properties_and_errors(self):
        self.stub("systemctl", f"""printf 'MainPID={os.getpid()}\nActiveState=active\n'
printf 'Environment=secret_marker\n'
printf 'secret_marker' >&2
""")
        result = subprocess.run(
            [sys.executable, str(ROOT / "capacity.py")],
            env=self.env, capture_output=True, check=True,
        )
        snapshot = json.loads(result.stdout)
        self.assertIsInstance(snapshot["master_direct_child_count"], int)
        self.assertNotIn(b"secret_marker", result.stdout + result.stderr)
        self.assertNotIn(b"pid", result.stdout.replace(b"pids.", b""))

    def test_modified_sql_rejected_before_psql(self):
        self.stub("psql", "touch \"$0.called\"\n")
        for query in (
            b"CREATE TABLE should_not_run (id int);",
            b"UPDATE accounts SET balance = 0;",
            b"ALTER SYSTEM SET max_connections = 10;",
            b"LOCK TABLE accounts IN ACCESS EXCLUSIVE MODE;",
            b"\\! touch /tmp/probe-should-not-run",
        ):
            with self.subTest(query=query):
                result = self.postgres(SQL + query)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout), {"error": "unapproved_sql"})
        self.assertFalse((self.bin / "psql.called").exists())

    def test_pinned_sql_and_read_only_client_options(self):
        self.stub("psql", """case "$PGOPTIONS" in
    *default_transaction_read_only=on*statement_timeout=3000*lock_timeout=1000*) ;;
    *) exit 32 ;;
esac
case " $* " in
    *" -X "*" -w "*" -f - "*) ;;
    *) exit 33 ;;
esac
grep -qx 'BEGIN READ ONLY;' || exit 34
echo '30|3|0|4|0|1'
echo secret_marker >&2
""")
        result = self.postgres(SQL)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["max_connections"], 30)
        self.assertEqual(result.stderr, b"")

    def test_postgres_output_and_failure_are_sanitized(self):
        for code, error in (
            ("echo secret_marker; exit 0", "unexpected_postgres_output"),
            ("echo secret_marker >&2; exit 1", "postgres_probe_failed"),
        ):
            self.stub("psql", f"cat >/dev/null; {code}\n")
            result = self.postgres(SQL)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {"error": error})
            self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
