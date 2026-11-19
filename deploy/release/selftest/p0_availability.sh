#!/usr/bin/env bash
# Run in a disposable container with nginx, PostgreSQL 16 and the lock wheelhouse.
set -euo pipefail
[ -f /.dockerenv ] && [ "$(id -u)" = 0 ] && [ "$PWD" = /repo ]
pip install -q --no-index --find-links /deps/wheelhouse --require-hashes --no-deps -r /deps/requirements.lock
useradd --system rastisi-fixture
install -d -m 0700 -o rastisi-fixture /run/rastisi-gunicorn
mkdir -p /test/app /test/home
chmod 0555 /test/home
pg_ctlcluster 16 main start
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c 'CREATE ROLE "rastisi-fixture" LOGIN'
cat >/test/app/wsgi.py <<'PY'
import psycopg

def app(environ, start_response):
    with psycopg.connect("dbname=postgres connect_timeout=2") as connection:
        connection.execute("SELECT 1").fetchone()
    start_response("200 OK", [("Content-Type", "text/html")])
    return [b"Gunicorn fixture backed by PostgreSQL"]
PY
cat >/test/nginx.conf <<'EOF'
user www-data;
pid /test/nginx.pid;
error_log /test/nginx.log;
events {}
http {
    include /repo/deploy/release/examples/nginx-availability-http.conf;
    server {
        listen 127.0.0.1:18080;
        access_log /test/availability.jsonl rastisi_availability;
        location / {
            proxy_connect_timeout 1s;
            proxy_read_timeout 1s;
            proxy_set_header X-Request-ID $request_id;
            proxy_pass http://127.0.0.1:18082;
        }
    }
}
EOF
nginx -c /test/nginx.conf
# Copy from the mounted checkout so fixture UID can read it on a 0700 workspace.
install -m 0644 /repo/deploy/release/examples/gunicorn-availability.py /test/app/gunicorn-availability.py
gunicorn=/opt/test-venv/bin/gunicorn
pid_file=/run/rastisi-gunicorn/master
start_gunicorn() {
    (
        cd /test/app
        runuser -u rastisi-fixture -- env "$@" HOME=/test/home "$gunicorn" \
            --config /test/app/gunicorn-availability.py --threads 2 --timeout 3 --bind 127.0.0.1:18082 --pid "$pid_file" wsgi:app
    ) >>/test/gunicorn.log 2>&1 &
}
get() { curl -sS --max-time 2 -o /test/response -w '%{http_code}' http://127.0.0.1:18080/; }
wait_for_200() {
    for attempt in {1..40}; do
        if [ "$(get)" = 200 ]; then
            grep -q 'Gunicorn fixture backed by PostgreSQL' /test/response
            return
        fi
        sleep 0.25
    done
    cat /test/gunicorn.log
    return 1
}
start_gunicorn -u XDG_RUNTIME_DIR
wait_for_200
grep -q 'Control server error.*Permission denied' /test/gunicorn.log
kill -TERM "$(cat "$pid_file")"
for attempt in {1..40}; do
    [ -e "$pid_file" ] || break
    sleep 0.25
done
test ! -e "$pid_file"
: >/test/gunicorn.log
start_gunicorn XDG_RUNTIME_DIR=/run/rastisi-gunicorn
wait_for_200
test -S /run/rastisi-gunicorn/gunicorn.ctl
test "$(stat -c %a /run/rastisi-gunicorn)" = 700
if grep -q 'Control server error' /test/gunicorn.log; then exit 1; fi
worker="$(pgrep -P "$(cat "$pid_file")")"
kill -STOP "$worker"
test "$(get)" = 504
wait_for_200
grep -q "WORKER TIMEOUT (pid:$worker)" /test/gunicorn.log
test "$(pgrep -P "$(cat "$pid_file")")" != "$worker"
echo 'PASS Gunicorn control socket, PostgreSQL read, Nginx upstream 504 and worker recovery'

test "$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' \
    -H 'Cookie: synthetic-private-cookie' -H 'Authorization: Bearer synthetic-private-auth' \
    -H 'X-Request-ID: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
    'http://127.0.0.1:18080/synthetic-private-path?token=synthetic-private-query')" = 200
python - <<'END'
import json
from pathlib import Path

access = Path("/test/availability.jsonl").read_text()
worker = Path("/test/gunicorn.log").read_text()
assert "synthetic-private-" not in access + worker
records = list(map(json.loads, access.splitlines()))
served = records[-1]
assert served["status"] == "200" and served["upstream_addr"] == "127.0.0.1:18082", served
assert served["request_id"] != "a" * 32
assert float(served["upstream_response_time"]) >= 0
events = [json.loads(line[line.index("{"):]) for line in worker.splitlines()
          if '{"format":"rastisi_worker_v1"' in line]
matched = [row for row in events if row.get("request_id") == served["request_id"]]
assert [row["event"] for row in matched] == ["request_started", "request_finished"], matched
assert matched[0]["pid"] == matched[1]["pid"]
assert matched[1]["duration_ms"] >= 0
assert any(record["status"] == "504" and record["upstream_addr"] == "127.0.0.1:18082"
           for record in records)
print("PASS private Nginx/Gunicorn request correlation, duration, 504 and redaction")
END
