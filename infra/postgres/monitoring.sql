\getenv exporter_password POSTGRES_EXPORTER_PASSWORD

BEGIN;
SET LOCAL pg_stat_statements.track_utility = off;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

DO $$
BEGIN
    IF current_user = 'lab_metrics' OR EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'lab_metrics'
        AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    ) THEN
        RAISE EXCEPTION 'lab_metrics is reserved for the monitoring role';
    END IF;
END
$$;

SELECT 'CREATE ROLE lab_metrics LOGIN' WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'lab_metrics'
) \gexec

SELECT format('ALTER ROLE lab_metrics PASSWORD %L', :'exporter_password') \gexec
GRANT pg_monitor TO lab_metrics;
SELECT format('GRANT CONNECT ON DATABASE %I TO lab_metrics', current_database()) \gexec
COMMIT;
