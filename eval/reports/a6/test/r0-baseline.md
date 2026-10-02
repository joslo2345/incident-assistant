# Eval `test` · r0-baseline

Commit `1b0f0d0` (dirty) · model `qwen3-agent` · prompt `81e8c0d7431f` · judge `gemma3:12b` · 2026-10-02T00:14:36+00:00

| Metric | Value |
| --- | --- |
| Runs with a valid diagnosis | 25/25 |
| **Root-cause accuracy** | **19/25 (76%)** |
| Root-cause accuracy, BMC logs missing | 9/14 (64%) |
| Action allowed by the runbook | 15/25 (60%) |
| Unsafe actions (hardware action on a decoy) | 0/3 (0%) |
| Missed actions (real fault left in service) | 8/22 (36%) |
| Cites the runbook for the true type | 16/25 (64%) |
| Citation support (judge) | 96% |
| Latency p50 / p95 | 144.5 s / 271.9 s |
| Tokens per case | 24,008 |
| Cost per case | $0.0 |
| Tool errors / tool calls recovered from text | 12 / 9 |

| Type | Cases | Root cause | Action |
| --- | --- | --- | --- |
| ecc_degradation | 4 | 3 | 3 |
| gpu_off_bus | 4 | 4 | 3 |
| noisy_neighbor | 3 | 3 | 3 |
| nvlink_degradation | 3 | 3 | 3 |
| pcie_degradation | 3 | 3 | 2 |
| power_fault | 4 | 2 | 1 |
| thermal_runaway | 4 | 1 | 0 |

| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a6test/f001-pcie_degradation | pcie_degradation | yes | pcie_degradation | drain_node | yes | 100% | succeeded | 99 |
| a6test/f022-thermal_runaway | thermal_runaway | yes | noisy_neighbor ✗ | monitor ✗ | no | 100% | succeeded | 155 |
| a6test/f006-ecc_degradation | ecc_degradation | yes | ecc_degradation | replace_part | yes | 100% | succeeded | 184 |
| a6test/f014-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | drain_node | yes | 100% | succeeded | 114 |
| a6test/f008-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | reset_gpu ✗ | yes | 100% | succeeded | 135 |
| a6test/f018-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 83 |
| a6test/f009-nvlink_degradation | nvlink_degradation |  | nvlink_degradation | drain_node | yes | 100% | succeeded | 128 |
| a6test/f013-power_fault | power_fault |  | power_fault | drain_node | no | 100% | succeeded | 163 |
| a6test/f024-pcie_degradation | pcie_degradation |  | pcie_degradation | reset_gpu ✗ | no | 0% | succeeded | 197 |
| a6test/f000-power_fault | power_fault | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 272 |
| a6test/f020-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 100% | succeeded | 153 |
| a6test/f021-power_fault | power_fault |  | power_fault | monitor ✗ | no | 100% | succeeded | 182 |
| a6test/f012-pcie_degradation | pcie_degradation | yes | pcie_degradation | replace_part | yes | 100% | succeeded | 140 |
| a6test/f017-ecc_degradation | ecc_degradation |  | ecc_degradation | drain_node | yes | 100% | succeeded | 176 |
| a6test/f007-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | drain_node | yes | 100% | succeeded | 139 |
| a6test/f015-ecc_degradation | ecc_degradation |  | noisy_neighbor ✗ | monitor ✗ | no | 100% | succeeded | 132 |
| a6test/f003-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 145 |
| a6test/f016-thermal_runaway | thermal_runaway | yes | noisy_neighbor ✗ | monitor ✗ | no | 100% | succeeded | 130 |
| a6test/f011-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 100 |
| a6test/f005-thermal_runaway | thermal_runaway | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 257 |
| a6test/f002-power_fault | power_fault | yes | unknown ✗ | none ✗ | no | 100% | succeeded | 316 |
| a6test/f019-thermal_runaway | thermal_runaway | yes | thermal_runaway | monitor ✗ | yes | 100% | succeeded | 123 |
| a6testnn/f007-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 123 |
| a6testnn/f004-noisy_neighbor | noisy_neighbor |  | noisy_neighbor | none | yes | 100% | succeeded | 163 |
| a6testnn/f002-noisy_neighbor | noisy_neighbor |  | noisy_neighbor | none | yes | 100% | succeeded | 167 |
