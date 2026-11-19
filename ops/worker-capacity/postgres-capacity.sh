#!/bin/sh
# The owner selects the approved PostgreSQL connection via libpq before running.
set -eu
if [ "$#" -ne 0 ]; then
    printf '%s\n' '{"error":"invalid_arguments"}'
    exit 2
fi
sql=$(cat)
digest=$(printf '%s\n' "$sql" | sha256sum)
if [ "${digest%% *}" != "8ab2126b0c69e5f4bedac44f89dcf302aada906c6694ab30f004e8eeab58e3a2" ]; then
    printf '%s\n' '{"error":"unapproved_sql"}'
    exit 2
fi
if ! row=$(printf '%s\n' "$sql" | PGCONNECT_TIMEOUT=5 PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000 -c search_path=pg_catalog" timeout 12s psql -X -qAt -F '|' -w -v ON_ERROR_STOP=1 \
    -f - 2>/dev/null); then
    printf '%s\n' '{"error":"postgres_probe_failed"}'
    exit 1
fi
if ! printf '%s\n' "$row" | awk -F '|' '
    NF != 6 || NR != 1 { bad = 1 }
    { for (i = 1; i <= NF; i++) if ($i !~ /^[0-9]+$/) bad = 1 }
    END { exit (bad || NR != 1) }
'; then
    printf '%s\n' '{"error":"unexpected_postgres_output"}'
    exit 1
fi
printf '%s\n' "$row" | awk -F '|' '{
    printf "{\"max_connections\":%s,\"superuser_reserved_connections\":%s,\"reserved_connections\":%s,\"client_connections_including_probe\":%s,\"walsenders\":%s,\"other_active_clients\":%s}\n", $1,$2,$3,$4,$5,$6
}'
