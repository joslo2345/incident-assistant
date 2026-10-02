# Eval `dev` · r3-runbook-actions

Commit `21550f0` · model `qwen3-agent` · prompt `8c3be535ecec` · judge `gemma3:12b (v2)` · 2026-10-02T03:53:46+00:00

| Metric | Value |
| --- | --- |
| Runs with a valid diagnosis | 25/25 |
| **Root-cause accuracy** | **22/25 (88%)** |
| Root-cause accuracy, BMC logs missing | 12/13 (92%) |
| Action allowed by the runbook | 24/25 (96%) |
| Unsafe actions (hardware action on a decoy) | 0/3 (0%) |
| Missed actions (real fault left in service) | 1/22 (4%) |
| Cites the runbook for the true type | 18/25 (72%) |
| Citation support (judge) | 88% |
| Latency p50 / p95 | 123.6 s / 206.5 s |
| Tokens per case | 24,724 |
| Cost per case | $0.0 |
| Tool errors / tool calls recovered from text | 17 / 9 |

| Type | Cases | Root cause | Action |
| --- | --- | --- | --- |
| ecc_degradation | 4 | 3 | 3 |
| gpu_off_bus | 4 | 4 | 4 |
| noisy_neighbor | 3 | 2 | 3 |
| nvlink_degradation | 3 | 2 | 3 |
| pcie_degradation | 3 | 3 | 3 |
| power_fault | 4 | 4 | 4 |
| thermal_runaway | 4 | 4 | 4 |

| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a6dev/f021-pcie_degradation | pcie_degradation | yes | pcie_degradation | drain_node | yes | 100% | succeeded | 122 |
| a6dev/f012-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 115 |
| a6dev/f023-power_fault | power_fault | yes | power_fault | drain_node | no | 100% | succeeded | 190 |
| a6dev/f022-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | drain_node | yes | 100% | succeeded | 94 |
| a6dev/f020-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 102 |
| a6dev/f024-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 50% | succeeded | 128 |
| a6dev/f017-ecc_degradation | ecc_degradation | yes | ecc_degradation | replace_part | yes | 100% | succeeded | 103 |
| a6dev/f016-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 124 |
| a6dev/f008-power_fault | power_fault |  | power_fault | drain_node | no | 100% | succeeded | 206 |
| a6dev/f011-power_fault | power_fault | yes | power_fault | drain_node | no | 100% | succeeded | 160 |
| a6dev/f001-ecc_degradation | ecc_degradation |  | ecc_degradation | drain_node | yes | 100% | succeeded | 158 |
| a6dev/f018-ecc_degradation | ecc_degradation |  | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 190 |
| a6dev/f000-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 113 |
| a6dev/f003-nvlink_degradation | nvlink_degradation |  | nvlink_degradation | drain_node | yes | 100% | succeeded | 119 |
| a6dev/f013-ecc_degradation | ecc_degradation | yes | ecc_degradation | replace_part | yes | 50% | succeeded | 141 |
| a6dev/f009-thermal_runaway | thermal_runaway | yes | thermal_runaway | drain_node | yes | 100% | succeeded | 125 |
| a6dev/f014-nvlink_degradation | nvlink_degradation | yes | pcie_degradation ✗ | drain_node | no | 100% | succeeded | 126 |
| a6dev/f010-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 112 |
| a6dev/f004-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 50% | succeeded | 105 |
| a6dev/f015-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 50% | succeeded | 118 |
| a6dev/f005-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 116 |
| a6dev/f006-power_fault | power_fault |  | power_fault | drain_node | no | 100% | succeeded | 115 |
| a6dev/f019-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 175 |
| a6dev/f007-noisy_neighbor | noisy_neighbor |  | unknown ✗ | none | no | 0% | succeeded | 224 |
| a6devnn/f001-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 132 |
