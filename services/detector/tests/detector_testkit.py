from dataclasses import replace
from datetime import UTC, datetime, timedelta

from detector.features import BmcRecord, GpuMinute, MinuteBatch, XidRecord

T0 = datetime(2026, 10, 1, tzinfo=UTC)
NODE = "h100-node-01"


def minute(i: int) -> datetime:
    return T0 + timedelta(minutes=i)


def gpu(i: int, g: int = 0, node: str = NODE, **overrides: object) -> GpuMinute:
    base = GpuMinute(
        minute=minute(i), node_id=node, gpu_index=g, gpu_model="NVIDIA H100 80GB HBM3",
        samples=6, temp_avg=65.0, temp_max=66.0, power_avg=600.0, power_limit=700.0,
        util_avg=85.0, clock_min=1950, thermal_throttled=0, power_capped=0, sbe=1, dbe=0,
        retired=0, pcie=0, nvlink=0,
    )  # fmt: skip
    return replace(base, **overrides)  # type: ignore[arg-type]


def batch(i: int, gpus: list[GpuMinute] | None = None, xids: list[XidRecord] | None = None,
          bmc: list[BmcRecord] | None = None) -> MinuteBatch:  # fmt: skip
    return MinuteBatch(minute(i), gpus or [], xids or [], bmc or [])


def bmc(i: int, **fields: object) -> BmcRecord:
    base = dict(time=minute(i), node_id=NODE, severity="critical", message_id="m", message="msg",
                sensor_type=None, sensor_name=None, entry_code="assert", reading=None)  # fmt: skip
    base.update(fields)
    return BmcRecord(**base)  # type: ignore[arg-type]
