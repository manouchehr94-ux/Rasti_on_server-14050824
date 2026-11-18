#!/usr/bin/env bash
# Disposable Ubuntu container from run_isolated.sh, with ACL tools and /deps.
set -euo pipefail
[ -f /.dockerenv ] && [ "$(id -u)" = 0 ] && [ "$PWD" = /repo ]
pip install -q --no-index --find-links /deps/wheelhouse --require-hashes --no-deps -r /deps/requirements.lock
useradd --system rastisi
useradd --system rastisi-deploy
useradd --system rastisi-media-aliases
pg_ctlcluster 16 main start
runuser -u postgres -- psql -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE rastisi_alias_reader LOGIN PASSWORD 'synthetic-alias-fixture';
CREATE DATABASE alias_fixture;
\connect alias_fixture
CREATE TABLE stores_store (id bigint PRIMARY KEY, public_id uuid, admin_subdomain text, platform_code text, status text, deletion_requested_at timestamptz);
CREATE TABLE stores_storedomain (store_id bigint REFERENCES stores_store(id), hostname text, domain_type text, verification_status text, retired_at timestamptz);
CREATE TABLE unrelated (secret text);
INSERT INTO stores_store VALUES (1,'00000000-0000-0000-0000-000000000001','fresh-store','freshcode','active',NULL);
INSERT INTO stores_store VALUES (2,'00000000-0000-0000-0000-000000000002','other-store','othercode','active',NULL);
INSERT INTO stores_storedomain VALUES (1,'fresh-public.rastisi.test','platform_subdomain','verified',NULL);
GRANT CONNECT ON DATABASE alias_fixture TO rastisi_alias_reader;
GRANT USAGE ON SCHEMA public TO rastisi_alias_reader;
GRANT SELECT ON stores_store, stores_storedomain TO rastisi_alias_reader;
ALTER ROLE rastisi_alias_reader IN DATABASE alias_fixture SET default_transaction_read_only = on;
SQL

base=/srv/rastisi
root=/srv/rastisi-operator/media-by-name
target="$base/shared/media/stores/00000000000000000000000000000001"
release="$base/releases/fixture"
job=rastisi-media-aliases
mkdir -p "$release" "$base/shared/media/stores" "$base/media-by-name" "$root"
chown rastisi-deploy:rastisi "$base" "$base/releases" "$release"
chmod 2750 "$base" "$base/releases"
chown rastisi:www-data "$base/shared/media" "$base/shared/media/stores"
chmod 2750 "$base/shared/media"
chmod 2755 "$base/shared/media/stores"
ln -s "$release" "$base/current"
mkdir "$release/app"
cp -a apps shop_core manage.py static "$release/app"
ln -s /opt/test-venv "$release/venv"
chmod 0750 /opt/test-venv
setfacl -Rm "u:$job:r-X" /opt/test-venv
chown -R rastisi-deploy:rastisi "$release/app"
chmod -R u=rx,g=rx,o= "$release/app"
chmod 0700 "$base/media-by-name"
for name in demo-arch02 demo-v5 thermco a6a69ggnv; do
    case "$name" in
        demo-arch02) uuid=00000000000000000000000000000003 ;;
        demo-v5) uuid=00000000000000000000000000000004 ;;
        *) uuid=00000000000000000000000000000005 ;;
    esac
    ln -s "$base/shared/media/stores/$uuid" "$base/media-by-name/$name"
    cp -P "$base/media-by-name/$name" "$root/$name"
done
setfacl -m "u:$job:--x" "$base" "$base/releases" "$base/shared/media"
setfacl -Rm "u:$job:r-X" "$release/app"
chmod 0755 /srv/rastisi-operator
chown "$job:$job" "$root"
chmod 0700 "$root"
fixture_password=synthetic-alias-fixture
db="postgresql://rastisi_alias_reader:${fixture_password}@127.0.0.1/alias_fixture"
run_alias() {
    (cd "$base/current/app"
    runuser -u "$job" -- env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
      DJANGO_DEBUG=False DJANGO_ALLOWED_HOSTS=rastisi.test DJANGO_SECRET_KEY=synthetic-task-only-secret \
      RASTISI_RATE_LIMIT_CACHE_URL=redis://127.0.0.1:1/0 RASTISI_PLATFORM_PRIMARY_HOST=rastisi.test \
      DJANGO_MEDIA_ROOT="$base/shared/media" DATABASE_URL="$db" \
      "$base/current/venv/bin/python" manage.py sync_media_aliases --alias-root "$1" "${@:2}")
}
if run_alias "$base/media-by-name" --apply; then exit 1; fi
run_alias "$root"
test ! -e "$root/.managed-aliases.json"
run_alias "$root" --apply
test "$(readlink "$root/fresh-store")" = "$target"
test "$(readlink "$root/fresh-public")" = "$target"
for user in www-data rastisi rastisi-deploy; do
    if runuser -u "$user" -- ls "$root"; then exit 1; fi
done
for query in 'SELECT * FROM unrelated' 'UPDATE stores_store SET status=status'; do
    if psql "$db" -v ON_ERROR_STOP=1 -c "$query"; then exit 1; fi
done
test "$(readlink "$root/other-store")" = "${target%01}02"
chmod u+w "$base/shared/media/stores"
runuser -u rastisi -- mkdir -m 0700 "$target"
runuser -u rastisi -- touch "$target/canary.png"
if runuser -u "$job" -- cat "$target/canary.png"; then exit 1; fi
run_alias "$root" --apply
run_alias "$root" --remove-managed --apply
test ! -L "$root/fresh-store"
for name in demo-arch02 demo-v5 thermco a6a69ggnv; do
    test "$(readlink "$root/$name")" = "$(readlink "$base/media-by-name/$name")"
done
test -f "$target/canary.png"
if command -v systemd-analyze >/dev/null; then
    install -d /etc/rastisi
    install -m 0600 /dev/null /etc/rastisi/media-aliases.env
    systemd-analyze verify deploy/media-aliases/*.service deploy/media-aliases/*.timer
fi
echo 'PASS permission blocker, least-privilege dry-run/apply, four manual links, rollback and PostgreSQL grants'

lib=/usr/local/lib
old=3-laptop-flat-bootstrap
new=4-resumable-staging
mkdir -p "$lib/rastisi" "$lib/rastisi-tools-test/smoke.d"
printf '%s\n' "$old" > "$lib/rastisi/TOOLING_VERSION"
cp deploy/release/rastisi_edge_probe.py "$lib/rastisi/rastisi_edge_probe.py"
while IFS= read -r file; do cp "deploy/release/$file" "$lib/rastisi-tools-test/"; done < deploy/release/tooling-files.txt
cp deploy/release/smoke.d/*.py "$lib/rastisi-tools-test/smoke.d/"
cp deploy/release/rastisi_gate.py "$lib/rastisi/rastisi_gate.py"
swap() { python deploy/release/exchange_tooling.py rastisi-tools-test "$@"; }
if swap unexpected "$new"; then exit 1; fi
swap "$old" "$new"
cmp deploy/release/migration_guard.py "$lib/rastisi/migration_guard.py"
cmp deploy/release/rastisi_edge_probe.py "$lib/rastisi/rastisi_edge_probe.py"
test -f "$lib/rastisi/release_upload.py"
swap "$new" "$old"
test "$(cat "$lib/rastisi/TOOLING_VERSION")" = "$old"
cmp deploy/release/rastisi_edge_probe.py "$lib/rastisi/rastisi_edge_probe.py"
echo 'PASS atomic tooling install and rollback'
