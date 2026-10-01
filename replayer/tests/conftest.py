from datetime import UTC, datetime
from pathlib import Path

import pytest

from replayer.fleet import Fleet
from replayer.synth import FleetSimulator
from replayer.workload import Workload

PACKAGE_DIR = Path(__file__).resolve().parents[1]
START = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="session")
def fleet() -> Fleet:
    return Fleet.load(PACKAGE_DIR / "fleet.toml")


@pytest.fixture(scope="session")
def workload() -> Workload:
    return Workload.load(PACKAGE_DIR / "data" / "alibaba_slice.csv")


@pytest.fixture
def sim(fleet: Fleet, workload: Workload) -> FleetSimulator:
    return FleetSimulator(fleet, workload, start=START, seed=42)
