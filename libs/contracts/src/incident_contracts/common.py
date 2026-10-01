"""Shared types used by every contract."""

from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Final[Literal["1.0"]] = "1.0"

# Node IDs look like hostnames: "gpu-node-03", "rack2-n17".
NodeId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")]

# Index of the GPU within its node. 16 covers HGX-style 8-GPU boards with headroom.
GpuIndex = Annotated[int, Field(ge=0, lt=16)]


class Contract(BaseModel):
    """Base for all wire models: strict about unknown fields, immutable once built."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class FailureType(StrEnum):
    """Root-cause categories shared by the fault injector, detector, and agent."""

    THERMAL_RUNAWAY = "thermal_runaway"  # fan failure or blocked airflow, temps climb, throttling
    ECC_DEGRADATION = "ecc_degradation"  # rising correctable ECC, leading to uncorrectable errors
    POWER_FAULT = "power_fault"  # PSU fault or power capping
    GPU_OFF_BUS = "gpu_off_bus"  # XID 79: GPU falls off the PCIe bus
    NVLINK_DEGRADATION = "nvlink_degradation"  # rising NVLink CRC/replay errors
    PCIE_DEGRADATION = "pcie_degradation"  # PCIe replays, link downtraining
    NOISY_NEIGHBOR = "noisy_neighbor"  # workload behavior that looks like a fault but isn't
    UNKNOWN = "unknown"
