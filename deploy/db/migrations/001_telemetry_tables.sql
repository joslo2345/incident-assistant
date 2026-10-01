-- Raw telemetry, one hypertable per event type.
-- Idempotency: the unique index on (event_id, time) makes redelivered events no-ops
-- (INSERT ... ON CONFLICT DO NOTHING). Unique indexes on hypertables must include the time column;
-- a retried event always carries the same timestamp, so this is equivalent to event_id alone.
-- ingested_at = Kafka record timestamp (set when ingest published); inserted_at = DB commit time.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE gpu_metrics (
    time                    timestamptz NOT NULL,
    node_id                 text        NOT NULL,
    gpu_index               smallint    NOT NULL,
    event_id                uuid        NOT NULL,
    gpu_uuid                text,
    gpu_model               text,
    temperature_c           real        NOT NULL,
    memory_temperature_c    real,
    power_w                 real        NOT NULL,
    power_limit_w           real        NOT NULL,
    utilization_pct         real        NOT NULL,
    memory_used_mib         integer     NOT NULL,
    memory_total_mib        integer     NOT NULL,
    sm_clock_mhz            integer     NOT NULL,
    throttle_reasons        text[]      NOT NULL DEFAULT '{}',
    ecc_sbe_total           bigint      NOT NULL,
    ecc_dbe_total           bigint      NOT NULL,
    retired_pages_total     bigint,
    pcie_replay_total       bigint,
    nvlink_crc_errors_total bigint,
    ingested_at             timestamptz NOT NULL,
    inserted_at             timestamptz NOT NULL DEFAULT now()
);
SELECT create_hypertable('gpu_metrics', by_range('time', INTERVAL '1 day'));
CREATE UNIQUE INDEX gpu_metrics_event_id ON gpu_metrics (event_id, time);
CREATE INDEX gpu_metrics_gpu_time ON gpu_metrics (node_id, gpu_index, time DESC);

CREATE TABLE xid_events (
    time        timestamptz NOT NULL,
    node_id     text        NOT NULL,
    gpu_index   smallint    NOT NULL,
    event_id    uuid        NOT NULL,
    xid_code    integer     NOT NULL,
    message     text        NOT NULL,
    ingested_at timestamptz NOT NULL,
    inserted_at timestamptz NOT NULL DEFAULT now()
);
SELECT create_hypertable('xid_events', by_range('time', INTERVAL '7 days'));
CREATE UNIQUE INDEX xid_events_event_id ON xid_events (event_id, time);
CREATE INDEX xid_events_node_time ON xid_events (node_id, time DESC);

CREATE TABLE bmc_log (
    time          timestamptz NOT NULL,
    node_id       text        NOT NULL,
    event_id      uuid        NOT NULL,
    source_format text        NOT NULL,
    entry_id      text,
    entry_type    text        NOT NULL,
    severity      text        NOT NULL,
    message_id    text        NOT NULL,
    message       text        NOT NULL,
    message_args  text[]      NOT NULL DEFAULT '{}',
    sensor_type   text,
    sensor_number smallint,
    sensor_name   text,
    entry_code    text,
    reading       real,
    unit          text,
    ingested_at   timestamptz NOT NULL,
    inserted_at   timestamptz NOT NULL DEFAULT now()
);
SELECT create_hypertable('bmc_log', by_range('time', INTERVAL '7 days'));
CREATE UNIQUE INDEX bmc_log_event_id ON bmc_log (event_id, time);
CREATE INDEX bmc_log_node_time ON bmc_log (node_id, time DESC);
CREATE INDEX bmc_log_message_id ON bmc_log (message_id, time DESC);
