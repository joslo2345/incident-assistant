-- Late and backfilled data (collector outages, replays with a past start time) arrived outside
-- the 2-hour refresh window of 002 and never reached the rollups. Real-time aggregation only covers
-- data newer than the materialization watermark, so it doesn't help for old buckets.
-- Refreshing over the full raw-retention window is cheap: TimescaleDB only recomputes buckets
-- whose raw data changed (it tracks invalidations), so an unchanged window costs almost nothing.

SELECT remove_continuous_aggregate_policy('gpu_metrics_1m');
SELECT add_continuous_aggregate_policy('gpu_metrics_1m',
    start_offset => INTERVAL '14 days', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');

SELECT remove_continuous_aggregate_policy('gpu_metrics_1h');
SELECT add_continuous_aggregate_policy('gpu_metrics_1h',
    start_offset => INTERVAL '14 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '5 minutes');
