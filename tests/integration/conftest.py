"""Integration tests run against the live Compose stack (`make up`), via `make test-integration`."""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", str(REPO / "deploy" / "compose.yaml")]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="needs the running stack; use `make test-integration`")
    for item in items:
        if "integration" in str(item.path):
            item.add_marker(skip)


def compose(*args: str, check: bool = True, input_text: str | None = None) -> str:
    result = subprocess.run(
        [*COMPOSE, *args], capture_output=True, text=True, check=check, input=input_text
    )
    return result.stdout.strip()


def sql(query: str) -> str:
    return compose(
        "exec", "-T", "timescaledb", "psql", "-U", "postgres", "-d", "telemetry", "-tAc", query
    )


def telemetry_rows() -> int:
    return int(
        sql(
            "SELECT (SELECT count(*) FROM gpu_metrics) + (SELECT count(*) FROM bmc_log) "
            "+ (SELECT count(*) FROM xid_events)"
        )
    )


def consumer_metric(name: str) -> float:
    """Sum of a counter across label sets, read from the consumer's /metrics endpoint."""
    script = (
        "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9101/metrics')"
        ".read().decode())"
    )
    text = compose("exec", "-T", "consumer", "python", "-c", script)
    return sum(
        float(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if line.startswith(name) and not line.startswith("#")
    )


def replay(*args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["uv", "run", "replay", *args], cwd=REPO, stdout=subprocess.PIPE, text=True
    )


def replay_stats(proc: subprocess.Popen[str], timeout: float = 300) -> dict[str, float]:
    out, _ = proc.communicate(timeout=timeout)
    assert proc.returncode == 0, out
    stats: dict[str, float] = json.loads(out)
    return stats
