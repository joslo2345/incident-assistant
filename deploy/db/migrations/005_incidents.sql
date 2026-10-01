-- Incidents opened by the detector (A3) and later diagnosed by the agent (A5).
-- `record` holds the full Incident contract (libs/contracts); the other columns are copies of
-- its fields for indexing and filtering. Times are event times (when the telemetry happened),
-- so backfilled runs score the same as live ones.
-- detector_run separates live detection ('live') from evaluation backfills (e.g. 'ev1-v3').

CREATE TABLE incidents (
    incident_id            uuid        PRIMARY KEY,
    detector_run           text        NOT NULL,
    node_id                text        NOT NULL,
    gpu_indices            smallint[]  NOT NULL DEFAULT '{}',
    status                 text        NOT NULL,
    severity               text        NOT NULL,
    suspected_failure_type text        NOT NULL,
    title                  text        NOT NULL,
    opened_at              timestamptz NOT NULL,  -- when the detector opened it
    first_signal_at        timestamptz NOT NULL,  -- earliest evidence
    last_signal_at         timestamptz NOT NULL,
    record                 jsonb       NOT NULL,
    written_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX incidents_run_opened ON incidents (detector_run, opened_at DESC);
CREATE INDEX incidents_node_opened ON incidents (node_id, opened_at DESC);
CREATE INDEX incidents_open ON incidents (status) WHERE status IN ('open', 'investigating');

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'incident_detector') THEN
        CREATE ROLE incident_detector NOLOGIN;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO incident_detector', current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO incident_detector;
GRANT SELECT ON gpu_metrics, xid_events, bmc_log, gpu_metrics_1m TO incident_detector;
-- DELETE only so an evaluation run can be re-run under the same detector_run.
GRANT SELECT, INSERT, UPDATE, DELETE ON incidents TO incident_detector;
GRANT SELECT ON incidents TO grafana_reader;
ALTER ROLE incident_detector SET statement_timeout = '120s';
