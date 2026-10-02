# Eval `dev` · r4-instruct-model

Commit `21550f0` (dirty) · model `qwen3-instruct-agent` · prompt `8c3be535ecec` · judge `gemma3:12b (v2)` · 2026-10-02T04:51:23+00:00

| Metric | Value |
| --- | --- |
| Runs with a valid diagnosis | 23/25 |
| **Root-cause accuracy** | **21/25 (84%)** |
| Root-cause accuracy, BMC logs missing | 11/13 (85%) |
| Action allowed by the runbook | 18/25 (72%) |
| Unsafe actions (hardware action on a decoy) | 0/3 (0%) |
| Missed actions (real fault left in service) | 5/22 (23%) |
| Cites the runbook for the true type | 18/25 (72%) |
| Citation support (judge) | 97% |
| Latency p50 / p95 | 61.0 s / 140.9 s |
| Tokens per case | 56,858 |
| Cost per case | $0.0 |
| Tool errors / tool calls recovered from text | 0 / 0 |

| Type | Cases | Root cause | Action |
| --- | --- | --- | --- |
| ecc_degradation | 4 | 3 | 3 |
| gpu_off_bus | 4 | 3 | 3 |
| noisy_neighbor | 3 | 3 | 3 |
| nvlink_degradation | 3 | 2 | 2 |
| pcie_degradation | 3 | 3 | 3 |
| power_fault | 4 | 4 | 1 |
| thermal_runaway | 4 | 3 | 3 |

| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a6dev/f021-pcie_degradation | pcie_degradation | yes | pcie_degradation | drain_node | yes | 100% | succeeded | 61 |
| a6dev/f012-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | reset_gpu | yes | 100% | succeeded | 91 |
| a6dev/f023-power_fault | power_fault | yes | power_fault | none ✗ | no | 100% | succeeded | 47 |
| a6dev/f022-gpu_off_bus | gpu_off_bus |  | - ✗ | - ✗ | no | - | invalid_output | 61 |
| a6dev/f020-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 46 |
| a6dev/f024-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 100% | succeeded | 141 |
| a6dev/f017-ecc_degradation | ecc_degradation | yes | ecc_degradation | replace_part | yes | 75% | succeeded | 63 |
| a6dev/f016-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 63 |
| a6dev/f008-power_fault | power_fault |  | power_fault | none ✗ | yes | 100% | succeeded | 50 |
| a6dev/f011-power_fault | power_fault | yes | power_fault | none ✗ | no | 100% | succeeded | 54 |
| a6dev/f001-ecc_degradation | ecc_degradation |  | ecc_degradation | replace_part | yes | 100% | succeeded | 80 |
| a6dev/f018-ecc_degradation | ecc_degradation |  | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 75 |
| a6dev/f000-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 72 |
| a6dev/f003-nvlink_degradation | nvlink_degradation |  | nvlink_degradation | drain_node | yes | 100% | succeeded | 49 |
| a6dev/f013-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 75% | succeeded | 91 |
| a6dev/f009-thermal_runaway | thermal_runaway | yes | - ✗ | - ✗ | no | - | invalid_output | 318 |
| a6dev/f014-nvlink_degradation | nvlink_degradation | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 71 |
| a6dev/f010-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 44 |
| a6dev/f004-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 75% | succeeded | 102 |
| a6dev/f015-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 100% | succeeded | 44 |
| a6dev/f005-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 44 |
| a6dev/f006-power_fault | power_fault |  | power_fault | drain_node | no | 100% | succeeded | 56 |
| a6dev/f019-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 70 |
| a6dev/f007-noisy_neighbor | noisy_neighbor |  | noisy_neighbor | none | yes | 100% | succeeded | 40 |
| a6devnn/f001-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 39 |
