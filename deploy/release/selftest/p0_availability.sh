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
    server {
        listen 127.0.0.1:18080;
        location / {
            proxy_connect_timeout 1s;
            proxy_read_timeout 1s;
            proxy_pass http://127.0.0.1:18082;
        }
    }
}
EOF
nginx -c /test/nginx.conf
gunicorn=/opt/test-venv/bin/gunicorn
pid_file=/run/rastisi-gunicorn/master
start_gunicorn() {
    (
        cd /test/app
        runuser -u rastisi-fixture -- env "$@" HOME=/test/home "$gunicorn" \
            --threads 2 --timeout 3 --bind 127.0.0.1:18082 --pid "$pid_file" wsgi:app
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
