# Eval `dev` · r2-fault-signals

Commit `240a447` · model `qwen3-agent` · prompt `340835ae3010` · judge `gemma3:12b (v2)` · 2026-10-02T02:51:06+00:00

| Metric | Value |
| --- | --- |
| Runs with a valid diagnosis | 25/25 |
| **Root-cause accuracy** | **22/25 (88%)** |
| Root-cause accuracy, BMC logs missing | 13/13 (100%) |
| Action allowed by the runbook | 19/25 (76%) |
| Unsafe actions (hardware action on a decoy) | 0/3 (0%) |
| Missed actions (real fault left in service) | 5/22 (23%) |
| Cites the runbook for the true type | 21/25 (84%) |
| Citation support (judge) | 84% |
| Latency p50 / p95 | 142.9 s / 275.5 s |
| Tokens per case | 24,803 |
| Cost per case | $0.0 |
| Tool errors / tool calls recovered from text | 13 / 3 |

| Type | Cases | Root cause | Action |
| --- | --- | --- | --- |
| ecc_degradation | 4 | 3 | 3 |
| gpu_off_bus | 4 | 4 | 4 |
| noisy_neighbor | 3 | 2 | 3 |
| nvlink_degradation | 3 | 3 | 3 |
| pcie_degradation | 3 | 3 | 2 |
| power_fault | 4 | 4 | 3 |
| thermal_runaway | 4 | 3 | 1 |

| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a6dev/f021-pcie_degradation | pcie_degradation | yes | pcie_degradation | drain_node | yes | 100% | succeeded | 152 |
| a6dev/f012-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 87 |
| a6dev/f023-power_fault | power_fault | yes | power_fault | drain_node | no | 100% | succeeded | 240 |
| a6dev/f022-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | drain_node | yes | 100% | succeeded | 108 |
| a6dev/f020-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 124 |
| a6dev/f024-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 100% | succeeded | 154 |
| a6dev/f017-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 50% | succeeded | 170 |
| a6dev/f016-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 50% | succeeded | 136 |
| a6dev/f008-power_fault | power_fault |  | power_fault | drain_node | no | 100% | succeeded | 197 |
| a6dev/f011-power_fault | power_fault | yes | power_fault | drain_node | no | 100% | succeeded | 166 |
| a6dev/f001-ecc_degradation | ecc_degradation |  | ecc_degradation | drain_node | yes | 50% | succeeded | 166 |
| a6dev/f018-ecc_degradation | ecc_degradation |  | unknown ✗ | none ✗ | yes | 0% | succeeded | 233 |
| a6dev/f000-pcie_degradation | pcie_degradation |  | pcie_degradation | reset_gpu ✗ | yes | 100% | succeeded | 119 |
| a6dev/f003-nvlink_degradation | nvlink_degradation |  | nvlink_degradation | drain_node | yes | 100% | succeeded | 118 |
| a6dev/f013-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 50% | succeeded | 146 |
| a6dev/f009-thermal_runaway | thermal_runaway | yes | thermal_runaway | monitor ✗ | yes | 50% | succeeded | 138 |
| a6dev/f014-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 110 |
| a6dev/f010-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 130 |
| a6dev/f004-thermal_runaway | thermal_runaway |  | thermal_runaway | monitor ✗ | yes | 100% | succeeded | 110 |
| a6dev/f015-thermal_runaway | thermal_runaway |  | unknown ✗ | none ✗ | yes | 100% | succeeded | 276 |
| a6dev/f005-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 50% | succeeded | 113 |
| a6dev/f006-power_fault | power_fault |  | power_fault | monitor ✗ | no | 100% | succeeded | 95 |
| a6dev/f019-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 143 |
| a6dev/f007-noisy_neighbor | noisy_neighbor |  | unknown ✗ | none | yes | 100% | succeeded | 168 |
| a6devnn/f001-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 277 |
