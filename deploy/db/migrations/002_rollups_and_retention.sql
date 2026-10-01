-- Continuous aggregates for dashboards and the detector, plus compression and retention.
-- Created WITH NO DATA so this migration can run inside a transaction; the refresh policies fill them.
-- materialized_only = false: queries also see the newest, not-yet-materialized data.

CREATE MATERIALIZED VIEW gpu_metrics_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 minute', time) AS bucket,
    node_id,
    gpu_index,
    last(gpu_model, time)                     AS gpu_model,
    count(*)                                  AS samples,
    avg(temperature_c)                        AS temp_avg,
    max(temperature_c)                        AS temp_max,
    avg(power_w)                              AS power_avg,
    max(power_w)                              AS power_max,
    max(power_limit_w)                        AS power_limit_w,
    avg(utilization_pct)                      AS util_avg,
    max(memory_used_mib)                      AS memory_used_max_mib,
    min(sm_clock_mhz)                         AS sm_clock_min_mhz,
    count(*) FILTER (WHERE cardinality(throttle_reasons) > 0) AS throttled_samples,
    max(ecc_sbe_total)                        AS ecc_sbe_total,
    max(ecc_dbe_total)                        AS ecc_dbe_total,
    max(pcie_replay_total)                    AS pcie_replay_total,
    max(nvlink_crc_errors_total)              AS nvlink_crc_errors_total
FROM gpu_metrics
GROUP BY bucket, node_id, gpu_index
WITH NO DATA;

SELECT add_continuous_aggregate_policy('gpu_metrics_1m',
    start_offset => INTERVAL '2 hours', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');

-- Hourly rollup built on the 1-minute one (hierarchical aggregate). Averages are weighted by samples.
CREATE MATERIALIZED VIEW gpu_metrics_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 hour', bucket) AS bucket,
    node_id,
    gpu_index,
    last(gpu_model, bucket)                               AS gpu_model,
    sum(samples)                                          AS samples,
    sum(temp_avg * samples) / sum(samples)                AS temp_avg,
    max(temp_max)                                         AS temp_max,
    sum(power_avg * samples) / sum(samples)               AS power_avg,
    max(power_max)                                        AS power_max,
    sum(util_avg * samples) / sum(samples)                AS util_avg,
    sum(throttled_samples)                                AS throttled_samples,
    max(ecc_sbe_total)                                    AS ecc_sbe_total,
    max(ecc_dbe_total)                                    AS ecc_dbe_total
FROM gpu_metrics_1m
GROUP BY 1, node_id, gpu_index
WITH NO DATA;

SELECT add_continuous_aggregate_policy('gpu_metrics_1h',
    start_offset => INTERVAL '1 day', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes');

-- Compress raw metrics after 2 days; queries by GPU stay fast because of segmentby.
-- TimescaleDB warns that event_id (in the unique index) isn't in segmentby/orderby. That only slows
-- conflict checks for events redelivered into chunks older than 2 days; redelivery takes minutes.
ALTER TABLE gpu_metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'node_id, gpu_index',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('gpu_metrics', INTERVAL '2 days');

-- Retention: raw metrics are big and the rollups keep the history.
SELECT add_retention_policy('gpu_metrics', INTERVAL '14 days');
SELECT add_retention_policy('gpu_metrics_1m', INTERVAL '90 days');
SELECT add_retention_policy('gpu_metrics_1h', INTERVAL '2 years');
SELECT add_retention_policy('xid_events', INTERVAL '1 year');
SELECT add_retention_policy('bmc_log', INTERVAL '1 year');
