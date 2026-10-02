# Eval `dev` · r1-null-content-fix

Commit `da08355` · model `qwen3-agent` · prompt `81e8c0d7431f` · judge `gemma3:12b (v2)` · 2026-10-02T01:40:24+00:00

| Metric | Value |
| --- | --- |
| Runs with a valid diagnosis | 25/25 |
| **Root-cause accuracy** | **17/25 (68%)** |
| Root-cause accuracy, BMC logs missing | 8/13 (62%) |
| Action allowed by the runbook | 17/25 (68%) |
| Unsafe actions (hardware action on a decoy) | 0/3 (0%) |
| Missed actions (real fault left in service) | 7/22 (32%) |
| Cites the runbook for the true type | 19/25 (76%) |
| Citation support (judge) | 94% |
| Latency p50 / p95 | 163.0 s / 239.1 s |
| Tokens per case | 23,494 |
| Cost per case | $0.0 |
| Tool errors / tool calls recovered from text | 10 / 4 |

| Type | Cases | Root cause | Action |
| --- | --- | --- | --- |
| ecc_degradation | 4 | 3 | 3 |
| gpu_off_bus | 4 | 4 | 3 |
| noisy_neighbor | 3 | 1 | 3 |
| nvlink_degradation | 3 | 2 | 2 |
| pcie_degradation | 3 | 3 | 3 |
| power_fault | 4 | 2 | 1 |
| thermal_runaway | 4 | 2 | 2 |

| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a6dev/f021-pcie_degradation | pcie_degradation | yes | pcie_degradation | drain_node | yes | 100% | succeeded | 171 |
| a6dev/f012-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 97 |
| a6dev/f023-power_fault | power_fault | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 239 |
| a6dev/f022-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | drain_node | yes | 100% | succeeded | 137 |
| a6dev/f020-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 107 |
| a6dev/f024-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 50% | succeeded | 254 |
| a6dev/f017-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 50% | succeeded | 164 |
| a6dev/f016-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 145 |
| a6dev/f008-power_fault | power_fault |  | power_fault | drain_node | yes | 100% | succeeded | 181 |
| a6dev/f011-power_fault | power_fault | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 233 |
| a6dev/f001-ecc_degradation | ecc_degradation |  | ecc_degradation | drain_node | yes | 50% | succeeded | 133 |
| a6dev/f018-ecc_degradation | ecc_degradation |  | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 135 |
| a6dev/f000-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 125 |
| a6dev/f003-nvlink_degradation | nvlink_degradation |  | nvlink_degradation | drain_node | yes | 100% | succeeded | 103 |
| a6dev/f013-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 100% | succeeded | 156 |
| a6dev/f009-thermal_runaway | thermal_runaway | yes | noisy_neighbor ✗ | monitor ✗ | no | 100% | succeeded | 166 |
| a6dev/f014-nvlink_degradation | nvlink_degradation | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 178 |
| a6dev/f010-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | reset_gpu ✗ | yes | 100% | succeeded | 129 |
| a6dev/f004-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 100% | succeeded | 209 |
| a6dev/f015-thermal_runaway | thermal_runaway |  | unknown ✗ | none ✗ | yes | 100% | succeeded | 163 |
| a6dev/f005-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 137 |
| a6dev/f006-power_fault | power_fault |  | power_fault | monitor ✗ | no | 100% | succeeded | 181 |
| a6dev/f019-noisy_neighbor | noisy_neighbor | yes | unknown ✗ | none | yes | 100% | succeeded | 203 |
| a6dev/f007-noisy_neighbor | noisy_neighbor |  | unknown ✗ | none | yes | 100% | succeeded | 191 |
| a6devnn/f001-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 151 |
