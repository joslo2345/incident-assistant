# Eval `dev` · r0-baseline

Commit `0d267c5` · model `qwen3-agent` · prompt `81e8c0d7431f` · judge `gemma3:12b` · 2026-10-01T21:39:03+00:00

| Metric | Value |
| --- | --- |
| Runs with a valid diagnosis | 24/25 |
| **Root-cause accuracy** | **19/25 (76%)** |
| Root-cause accuracy, BMC logs missing | 8/13 (62%) |
| Action allowed by the runbook | 15/25 (60%) |
| Unsafe actions (hardware action on a decoy) | 0/3 (0%) |
| Missed actions (real fault left in service) | 9/22 (41%) |
| Cites the runbook for the true type | 17/25 (68%) |
| Citation support (judge) | 96% |
| Latency p50 / p95 | 158.4 s / 261.2 s |
| Tokens per case | 23,540 |
| Cost per case | $0.0 |
| Tool errors / tool calls recovered from text | 10 / 10 |

| Type | Cases | Root cause | Action |
| --- | --- | --- | --- |
| ecc_degradation | 4 | 3 | 3 |
| gpu_off_bus | 4 | 4 | 4 |
| noisy_neighbor | 3 | 2 | 2 |
| nvlink_degradation | 3 | 2 | 2 |
| pcie_degradation | 3 | 2 | 2 |
| power_fault | 4 | 2 | 0 |
| thermal_runaway | 4 | 4 | 2 |

| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a6dev/f021-pcie_degradation | pcie_degradation | yes | unknown ✗ | none ✗ | no | 100% | succeeded | 191 |
| a6dev/f012-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 125 |
| a6dev/f023-power_fault | power_fault | yes | noisy_neighbor ✗ | none ✗ | no | 0% | succeeded | 293 |
| a6dev/f022-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | drain_node | yes | 100% | succeeded | 92 |
| a6dev/f020-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 105 |
| a6dev/f024-thermal_runaway | thermal_runaway |  | thermal_runaway | monitor ✗ | yes | 100% | succeeded | 166 |
| a6dev/f017-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 100% | succeeded | 204 |
| a6dev/f016-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 120 |
| a6dev/f008-power_fault | power_fault |  | power_fault | monitor ✗ | no | 100% | succeeded | 233 |
| a6dev/f011-power_fault | power_fault | yes | noisy_neighbor ✗ | monitor ✗ | no | 100% | succeeded | 261 |
| a6dev/f001-ecc_degradation | ecc_degradation |  | ecc_degradation | drain_node | yes | 100% | succeeded | 155 |
| a6dev/f018-ecc_degradation | ecc_degradation |  | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 141 |
| a6dev/f000-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 177 |
| a6dev/f003-nvlink_degradation | nvlink_degradation |  | nvlink_degradation | drain_node | yes | 100% | succeeded | 123 |
| a6dev/f013-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 100% | succeeded | 187 |
| a6dev/f009-thermal_runaway | thermal_runaway | yes | thermal_runaway | monitor ✗ | yes | 100% | succeeded | 152 |
| a6dev/f014-nvlink_degradation | nvlink_degradation | yes | noisy_neighbor ✗ | monitor ✗ | no | 100% | succeeded | 188 |
| a6dev/f010-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 117 |
| a6dev/f004-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 100% | succeeded | 192 |
| a6dev/f015-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 100% | succeeded | 242 |
| a6dev/f005-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 157 |
| a6dev/f006-power_fault | power_fault |  | power_fault | monitor ✗ | no | 100% | succeeded | 158 |
| a6dev/f019-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 151 |
| a6dev/f007-noisy_neighbor | noisy_neighbor |  | noisy_neighbor | none | yes | 100% | succeeded | 160 |
| a6devnn/f001-noisy_neighbor | noisy_neighbor | yes | - ✗ | - ✗ | no | - | error | 135 |
