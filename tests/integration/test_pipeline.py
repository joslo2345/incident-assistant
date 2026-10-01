"""Delivery guarantees of the ingest -> Redpanda -> consumer -> TimescaleDB pipeline."""

import time
import uuid

from .conftest import compose, consumer_metric, replay, replay_stats, sql, telemetry_rows


def wait_for_rows(expected: int, timeout_s: float = 180) -> int:
    deadline = time.monotonic() + timeout_s
    rows = telemetry_rows()
    while rows < expected and time.monotonic() < deadline:
        time.sleep(1)
        rows = telemetry_rows()
    time.sleep(3)  # anything extra (duplicates) would show up now
    return telemetry_rows()


def test_killing_the_consumer_mid_replay_loses_and_duplicates_nothing() -> None:
    before = telemetry_rows()
    # 2 simulated days at max speed (~550k events) keeps the pipeline busy for a while.
    proc = replay("--duration", "2d", "--speed", "0", "--start", "now-3d", "--seed", "7")
    time.sleep(4)
    compose("kill", "-s", "SIGKILL", "consumer")  # a crash, not a graceful shutdown
    time.sleep(3)
    compose("start", "consumer")
    stats = replay_stats(proc)

    assert stats["accepted"] == stats["events_sent"]
    after = wait_for_rows(before + int(stats["accepted"]))
    assert after - before == stats["accepted"], "every accepted event lands exactly once"
    assert sql("SELECT count(*) - count(DISTINCT event_id) FROM gpu_metrics") == "0"
    # Whether the kill landed mid-batch is timing-dependent; if it did, the restarted consumer
    # saw those messages again and skipped them. The next test forces that case.
    skipped = consumer_metric('consumer_rows_total{result="duplicate"')
    print(f"redelivered rows skipped after restart: {skipped:.0f}")


def test_invalid_message_goes_to_dlq_and_pipeline_continues() -> None:
    dlq_before = _high_watermark("telemetry.dlq")
    marker = str(uuid.uuid4())
    bad = f'{{"event_type": "gpu_metrics", "node_id": "{marker}"}}\n'  # rpk: one record per line
    compose("exec", "-T", "redpanda", "rpk", "topic", "produce", "telemetry.raw", "-k", "x",
            input_text=bad)  # fmt: skip
    deadline = time.monotonic() + 30
    while _high_watermark("telemetry.dlq") == dlq_before and time.monotonic() < deadline:
        time.sleep(1)
    assert _high_watermark("telemetry.dlq") == dlq_before + 1

    # Good data still flows afterwards.
    before = telemetry_rows()
    stats = replay_stats(replay("--duration", "10m", "--speed", "0", "--start", "now-2d"))
    assert wait_for_rows(before + int(stats["accepted"])) - before == stats["accepted"]


def _high_watermark(topic: str) -> int:
    out = compose("exec", "-T", "redpanda", "rpk", "topic", "describe", topic, "-p")
    # Columns: PARTITION LEADER EPOCH REPLICAS LOG-START-OFFSET HIGH-WATERMARK
    return sum(int(line.split()[-1]) for line in out.splitlines()[1:] if line.strip())


def test_redelivered_messages_are_not_written_twice() -> None:
    """Force redelivery: rewind the consumer group to before a replay it already wrote."""
    rewind_to_ms = int(time.time() * 1000) - 1000
    before = telemetry_rows()
    stats = replay_stats(replay("--duration", "10m", "--speed", "0", "--start", "now-1d"))
    written = wait_for_rows(before + int(stats["accepted"]))
    assert written - before == stats["accepted"]

    compose("stop", "consumer")
    compose(
        "exec", "-T", "redpanda", "rpk", "group", "seek", "telemetry-writer",
        "--to", str(rewind_to_ms), "--topics", "telemetry.raw",
    )  # fmt: skip
    compose("start", "consumer")

    deadline = time.monotonic() + 60
    skipped = 0.0
    while skipped < stats["accepted"] and time.monotonic() < deadline:
        time.sleep(1)
        skipped = consumer_metric('consumer_rows_total{result="duplicate"')
    assert skipped >= stats["accepted"], "every redelivered message was seen again"
    assert telemetry_rows() == written, "and none of them created a new row"
