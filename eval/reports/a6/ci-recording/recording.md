# Eval `ci-replay` · recording

Commit `db4c9a4` (dirty) · model `qwen3-agent` · prompt `8c3be535ecec` · judge `off` · 2026-10-02T07:02:48+00:00

| Metric | Value |
| --- | --- |
| Runs with a valid diagnosis | 10/10 |
| **Root-cause accuracy** | **10/10 (100%)** |
| Root-cause accuracy, BMC logs missing | 7/7 (100%) |
| Action allowed by the runbook | 9/10 (90%) |
| Unsafe actions (hardware action on a decoy) | 0/1 (0%) |
| Missed actions (real fault left in service) | 0/9 (0%) |
| Cites the runbook for the true type | 8/10 (80%) |
| Citation support (judge) | - |
| Latency p50 / p95 | 118.8 s / 150.1 s |
| Tokens per case | 24,249 |
| Cost per case | $0.0 |
| Tool errors / tool calls recovered from text | 7 / 3 |

| Type | Cases | Root cause | Action |
| --- | --- | --- | --- |
| ecc_degradation | 2 | 2 | 2 |
| gpu_off_bus | 1 | 1 | 1 |
| noisy_neighbor | 1 | 1 | 1 |
| nvlink_degradation | 1 | 1 | 1 |
| pcie_degradation | 1 | 1 | 1 |
| power_fault | 2 | 2 | 2 |
| thermal_runaway | 2 | 2 | 1 |

| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ci923342/f003-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | - | succeeded | 83 |
| ci923342/f007-noisy_neighbor | noisy_neighbor |  | noisy_neighbor | none | yes | - | succeeded | 102 |
| ci923342/f008-power_fault | power_fault | yes | power_fault | drain_node | no | - | succeeded | 142 |
| ci923342/f002-ecc_degradation | ecc_degradation | yes | ecc_degradation | replace_part | yes | - | succeeded | 127 |
| ci923342/f009-pcie_degradation | pcie_degradation | yes | pcie_degradation | drain_node | yes | - | succeeded | 111 |
| ci923342/f000-ecc_degradation | ecc_degradation | yes | ecc_degradation | replace_part | yes | - | succeeded | 126 |
| ci923342/f001-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | drain_node | yes | - | succeeded | 73 |
| ci923342/f004-thermal_runaway | thermal_runaway | yes | thermal_runaway | reset_gpu ✗ | yes | - | succeeded | 135 |
| ci923342/f005-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | - | succeeded | 150 |
| ci923342/f006-power_fault | power_fault | yes | power_fault | drain_node | no | - | succeeded | 98 |
