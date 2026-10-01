-- Least-privilege roles. Services stop using the superuser:
--   telemetry_writer: the consumer. SELECT + INSERT on raw tables, TEMP for its staging tables.
--   grafana_reader:   dashboards. SELECT only, read-only transactions, 30 s statement timeout.
-- Roles are created without a password (NOLOGIN). The migrate step enables login and sets
-- passwords from environment variables, so secrets never live in SQL files.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'telemetry_writer') THEN
        CREATE ROLE telemetry_writer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        CREATE ROLE grafana_reader NOLOGIN;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO telemetry_writer, grafana_reader', current_database());
    EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO telemetry_writer', current_database());
    EXECUTE format('REVOKE CREATE ON DATABASE %I FROM PUBLIC', current_database());
END
$$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO telemetry_writer, grafana_reader;

GRANT SELECT, INSERT ON gpu_metrics, xid_events, bmc_log TO telemetry_writer;
GRANT SELECT ON gpu_metrics, xid_events, bmc_log, gpu_metrics_1m, gpu_metrics_1h TO grafana_reader;

ALTER ROLE grafana_reader SET default_transaction_read_only = on;
ALTER ROLE grafana_reader SET statement_timeout = '30s';
ALTER ROLE telemetry_writer SET statement_timeout = '60s';
