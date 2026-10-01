"""Fleet description: which nodes exist and what GPU each one has."""

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GpuProfile:
    model: str  # as DCGM reports it
    power_idle_w: float
    power_limit_w: float
    memory_total_mib: int
    sm_clock_max_mhz: int
    sm_clock_idle_mhz: int
    # Steady-state die temperature = inlet + thermal_c_per_w * power.
    thermal_c_per_w: float
    # How quickly die temperature follows power (first-order time constant).
    thermal_tau_s: float
    memory_temp_offset_c: float
    # Temperature at which the GPU starts thermal slowdown (clocks drop).
    slowdown_temp_c: float


PROFILES: dict[str, GpuProfile] = {
    # Full load (700 W) with a 24 C inlet settles around 69 C.
    "h100_sxm": GpuProfile(
        model="NVIDIA H100 80GB HBM3",
        power_idle_w=70,
        power_limit_w=700,
        memory_total_mib=81_559,
        sm_clock_max_mhz=1980,
        sm_clock_idle_mhz=345,
        thermal_c_per_w=0.064,
        thermal_tau_s=90,
        memory_temp_offset_c=6,
        slowdown_temp_c=87,
    ),
    # Full load (400 W) with a 24 C inlet settles around 66 C.
    "a100_sxm": GpuProfile(
        model="NVIDIA A100-SXM4-80GB",
        power_idle_w=55,
        power_limit_w=400,
        memory_total_mib=81_920,
        sm_clock_max_mhz=1410,
        sm_clock_idle_mhz=210,
        thermal_c_per_w=0.105,
        thermal_tau_s=90,
        memory_temp_offset_c=5,
        slowdown_temp_c=85,
    ),
}


@dataclass(frozen=True)
class Node:
    node_id: str
    profile: GpuProfile
    gpus: int = 8


@dataclass(frozen=True)
class Fleet:
    nodes: tuple[Node, ...]
    sample_interval_s: int = 10
    inlet_temp_c: float = 24.0

    @classmethod
    def load(cls, path: Path) -> "Fleet":
        raw = tomllib.loads(path.read_text())
        nodes = []
        for entry in raw["nodes"]:
            name = entry["profile"]
            if name not in PROFILES:
                raise ValueError(f"unknown GPU profile {name!r}; known: {sorted(PROFILES)}")
            nodes.append(Node(entry["id"], PROFILES[name], entry.get("gpus", 8)))
        return cls(
            nodes=tuple(nodes),
            sample_interval_s=raw.get("sample_interval_s", 10),
            inlet_temp_c=raw.get("inlet_temp_c", 24.0),
        )
