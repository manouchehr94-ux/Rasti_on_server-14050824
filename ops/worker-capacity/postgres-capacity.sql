BEGIN READ ONLY;
SELECT current_setting('max_connections')::int AS max_connections,
       current_setting('superuser_reserved_connections')::int AS superuser_reserved_connections,
       COALESCE(current_setting('reserved_connections', true)::int, 0) AS reserved_connections,
       count(*) FILTER (WHERE backend_type = 'client backend') AS client_connections_including_probe,
       count(*) FILTER (WHERE backend_type = 'walsender') AS walsenders,
       count(*) FILTER (WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()
                       AND state = 'active') AS other_active_clients
FROM pg_catalog.pg_stat_activity;
ROLLBACK;
