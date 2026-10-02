# Eval `test` · final-r3

Commit `db4c9a4` · model `qwen3-agent` · prompt `8c3be535ecec` · judge `gemma3:12b (v2)` · 2026-10-02T06:03:25+00:00

| Metric | Value |
| --- | --- |
| Runs with a valid diagnosis | 25/25 |
| **Root-cause accuracy** | **22/25 (88%)** |
| Root-cause accuracy, BMC logs missing | 12/14 (86%) |
| Action allowed by the runbook | 23/25 (92%) |
| Unsafe actions (hardware action on a decoy) | 0/3 (0%) |
| Missed actions (real fault left in service) | 1/22 (4%) |
| Cites the runbook for the true type | 19/25 (76%) |
| Citation support (judge) | 88% |
| Latency p50 / p95 | 124.8 s / 287.0 s |
| Tokens per case | 27,458 |
| Cost per case | $0.0 |
| Tool errors / tool calls recovered from text | 18 / 10 |

| Type | Cases | Root cause | Action |
| --- | --- | --- | --- |
| ecc_degradation | 4 | 4 | 4 |
| gpu_off_bus | 4 | 3 | 3 |
| noisy_neighbor | 3 | 3 | 3 |
| nvlink_degradation | 3 | 3 | 3 |
| pcie_degradation | 3 | 3 | 3 |
| power_fault | 4 | 2 | 3 |
| thermal_runaway | 4 | 4 | 4 |

| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a6test/f001-pcie_degradation | pcie_degradation | yes | pcie_degradation | drain_node | yes | 100% | succeeded | 116 |
| a6test/f022-thermal_runaway | thermal_runaway | yes | thermal_runaway | drain_node | yes | 50% | succeeded | 114 |
| a6test/f006-ecc_degradation | ecc_degradation | yes | ecc_degradation | replace_part | yes | 100% | succeeded | 167 |
| a6test/f014-gpu_off_bus | gpu_off_bus |  | pcie_degradation ✗ | drain_node | no | 100% | succeeded | 96 |
| a6test/f008-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | drain_node | yes | 100% | succeeded | 79 |
| a6test/f018-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 102 |
| a6test/f009-nvlink_degradation | nvlink_degradation |  | nvlink_degradation | drain_node | yes | 100% | succeeded | 89 |
| a6test/f013-power_fault | power_fault |  | power_fault | replace_part | no | 100% | succeeded | 213 |
| a6test/f024-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 162 |
| a6test/f000-power_fault | power_fault | yes | unknown ✗ | none ✗ | no | 100% | succeeded | 233 |
| a6test/f020-ecc_degradation | ecc_degradation | yes | ecc_degradation | replace_part | yes | 100% | succeeded | 121 |
| a6test/f021-power_fault | power_fault |  | power_fault | drain_node | no | 100% | succeeded | 118 |
| a6test/f012-pcie_degradation | pcie_degradation | yes | pcie_degradation | drain_node | yes | 100% | succeeded | 131 |
| a6test/f017-ecc_degradation | ecc_degradation |  | ecc_degradation | replace_part | yes | 100% | succeeded | 115 |
| a6test/f007-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | reset_gpu ✗ | yes | 100% | succeeded | 160 |
| a6test/f015-ecc_degradation | ecc_degradation |  | ecc_degradation | replace_part | yes | 100% | succeeded | 123 |
| a6test/f003-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 129 |
| a6test/f016-thermal_runaway | thermal_runaway | yes | thermal_runaway | drain_node | yes | 50% | succeeded | 125 |
| a6test/f011-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 113 |
| a6test/f005-thermal_runaway | thermal_runaway | yes | thermal_runaway | drain_node | yes | 50% | succeeded | 183 |
| a6test/f002-power_fault | power_fault | yes | unknown ✗ | escalate | no | 100% | succeeded | 367 |
| a6test/f019-thermal_runaway | thermal_runaway | yes | thermal_runaway | drain_node | yes | 50% | succeeded | 147 |
| a6testnn/f007-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 123 |
| a6testnn/f004-noisy_neighbor | noisy_neighbor |  | noisy_neighbor | none | yes | 100% | succeeded | 287 |
| a6testnn/f002-noisy_neighbor | noisy_neighbor |  | noisy_neighbor | none | no | 0% | succeeded | 281 |
