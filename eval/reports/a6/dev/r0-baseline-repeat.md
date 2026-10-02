# Eval `dev` · r0-baseline-repeat

Commit `1b0f0d0` · model `qwen3-agent` · prompt `81e8c0d7431f` · judge `gemma3:12b` · 2026-10-01T23:02:30+00:00

| Metric | Value |
| --- | --- |
| Runs with a valid diagnosis | 25/25 |
| **Root-cause accuracy** | **18/25 (72%)** |
| Root-cause accuracy, BMC logs missing | 9/13 (69%) |
| Action allowed by the runbook | 18/25 (72%) |
| Unsafe actions (hardware action on a decoy) | 0/3 (0%) |
| Missed actions (real fault left in service) | 7/22 (32%) |
| Cites the runbook for the true type | 16/25 (64%) |
| Citation support (judge) | 100% |
| Latency p50 / p95 | 178.3 s / 270.6 s |
| Tokens per case | 22,927 |
| Cost per case | $0.0 |
| Tool errors / tool calls recovered from text | 7 / 5 |

| Type | Cases | Root cause | Action |
| --- | --- | --- | --- |
| ecc_degradation | 4 | 3 | 3 |
| gpu_off_bus | 4 | 4 | 4 |
| noisy_neighbor | 3 | 3 | 3 |
| nvlink_degradation | 3 | 2 | 2 |
| pcie_degradation | 3 | 2 | 2 |
| power_fault | 4 | 2 | 1 |
| thermal_runaway | 4 | 2 | 3 |

| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a6dev/f021-pcie_degradation | pcie_degradation | yes | pcie_degradation | drain_node | yes | 100% | succeeded | 130 |
| a6dev/f012-nvlink_degradation | nvlink_degradation | yes | nvlink_degradation | drain_node | yes | 100% | succeeded | 210 |
| a6dev/f023-power_fault | power_fault | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 328 |
| a6dev/f022-gpu_off_bus | gpu_off_bus |  | gpu_off_bus | drain_node | yes | 100% | succeeded | 223 |
| a6dev/f020-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 224 |
| a6dev/f024-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 100% | succeeded | 232 |
| a6dev/f017-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 100% | succeeded | 201 |
| a6dev/f016-pcie_degradation | pcie_degradation |  | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 204 |
| a6dev/f008-power_fault | power_fault |  | power_fault | drain_node | no | 100% | succeeded | 202 |
| a6dev/f011-power_fault | power_fault | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 177 |
| a6dev/f001-ecc_degradation | ecc_degradation |  | ecc_degradation | drain_node | yes | 100% | succeeded | 133 |
| a6dev/f018-ecc_degradation | ecc_degradation |  | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 152 |
| a6dev/f000-pcie_degradation | pcie_degradation |  | pcie_degradation | drain_node | yes | 100% | succeeded | 146 |
| a6dev/f003-nvlink_degradation | nvlink_degradation |  | nvlink_degradation | drain_node | yes | 100% | succeeded | 123 |
| a6dev/f013-ecc_degradation | ecc_degradation | yes | ecc_degradation | drain_node | yes | 100% | succeeded | 193 |
| a6dev/f009-thermal_runaway | thermal_runaway | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 153 |
| a6dev/f014-nvlink_degradation | nvlink_degradation | yes | noisy_neighbor ✗ | none ✗ | no | 100% | succeeded | 178 |
| a6dev/f010-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 114 |
| a6dev/f004-thermal_runaway | thermal_runaway |  | thermal_runaway | drain_node | yes | 100% | succeeded | 144 |
| a6dev/f015-thermal_runaway | thermal_runaway |  | unknown ✗ | drain_node | no | 100% | succeeded | 271 |
| a6dev/f005-gpu_off_bus | gpu_off_bus | yes | gpu_off_bus | drain_node | yes | 100% | succeeded | 126 |
| a6dev/f006-power_fault | power_fault |  | power_fault | monitor ✗ | no | 100% | succeeded | 140 |
| a6dev/f019-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 199 |
| a6dev/f007-noisy_neighbor | noisy_neighbor |  | noisy_neighbor | monitor | yes | 100% | succeeded | 153 |
| a6devnn/f001-noisy_neighbor | noisy_neighbor | yes | noisy_neighbor | none | yes | 100% | succeeded | 182 |
