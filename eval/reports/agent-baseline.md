# Agent baseline: `ev3-v3-residual`

Model `qwen3-agent`. 26 actionable incidents investigated, 26 matched to an injected fault.

| Metric | Value |
| --- | --- |
| incidents investigated | 26 |
| matched to a fault | 26 |
| status | {'succeeded': 26} |
| root cause agent | 18/26 |
| root cause detector rules | 26/26 |
| root cause agent bmc dropped | 6/13 |
| cites runbook for true type | 17/26 |
| actions | {'drain_node': 13, 'none': 5, 'monitor': 7, 'replace_part': 1} |
| latency s p50 | 126 |
| latency s p95 | 187 |
| steps mean | 4.3 |
| tool calls mean | 3.3 |
| tokens mean | 22844 |
| cost usd total | 0.0 |
| rejected submissions | 0 |
| tool errors | 12 |
| tool calls recovered from text | 10 |

| Node | Truth | BMC dropped | Detector | Agent | Conf. | Action | Cited | Status | Steps | Latency (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ev3-a100-node-01 | gpu_off_bus |  | gpu_off_bus | gpu_off_bus | 0.95 | drain_node | rb-004-gpu-off-bus | succeeded | 5 | 101 |
| ev3-h100-node-05 | thermal_runaway | yes | thermal_runaway | noisy_neighbor ✗ | 0.9 | none | rb-007-noisy-neighbor-triage | succeeded | 4 | 155 |
| ev3-h100-node-01 | power_fault | yes | power_fault | noisy_neighbor ✗ | 0.9 | none | rb-007-noisy-neighbor-triage, rb-022-detector-alerts-guide | succeeded | 4 | 160 |
| ev3-a100-node-02 | thermal_runaway | yes | thermal_runaway | noisy_neighbor ✗ | 0.9 | monitor | rb-007-noisy-neighbor-triage | succeeded | 4 | 96 |
| ev3-h100-node-02 | thermal_runaway | yes | thermal_runaway | noisy_neighbor ✗ | 0.95 | monitor | rb-007-noisy-neighbor-triage | succeeded | 5 | 129 |
| ev3-h100-node-03 | pcie_degradation |  | pcie_degradation | pcie_degradation | 0.9 | drain_node | rb-006-pcie-degradation | succeeded | 5 | 114 |
| ev3-a100-node-01 | gpu_off_bus | yes | gpu_off_bus | gpu_off_bus | 0.95 | drain_node | rb-004-gpu-off-bus | succeeded | 5 | 102 |
| ev3-a100-node-01 | nvlink_degradation |  | nvlink_degradation | nvlink_degradation | 0.9 | drain_node | rb-005-nvlink-degradation | succeeded | 5 | 113 |
| ev3-h100-node-05 | nvlink_degradation |  | nvlink_degradation | nvlink_degradation | 0.95 | drain_node | rb-005-nvlink-degradation | succeeded | 4 | 100 |
| ev3-h100-node-03 | thermal_runaway | yes | thermal_runaway | noisy_neighbor ✗ | 0.9 | none | rb-007-noisy-neighbor-triage | succeeded | 4 | 160 |
| ev3-a100-node-02 | power_fault | yes | power_fault | noisy_neighbor ✗ | 0.95 | none | rb-007-noisy-neighbor-triage | succeeded | 4 | 157 |
| ev3-h100-node-05 | pcie_degradation | yes | pcie_degradation | pcie_degradation | 0.9 | drain_node | rb-006-pcie-degradation | succeeded | 5 | 94 |
| ev3-a100-node-02 | nvlink_degradation |  | nvlink_degradation | nvlink_degradation | 0.9 | drain_node | rb-005-nvlink-degradation | succeeded | 4 | 87 |
| ev3-h100-node-04 | ecc_degradation | yes | ecc_degradation | ecc_degradation | 0.9 | drain_node | rb-002-ecc-degradation | succeeded | 4 | 139 |
| ev3-h100-node-05 | ecc_degradation | yes | ecc_degradation | unknown ✗ | 0.3 | monitor | rb-002-ecc-degradation | succeeded | 4 | 157 |
| ev3-h100-node-05 | ecc_degradation | yes | ecc_degradation | ecc_degradation | 0.95 | drain_node | rb-002-ecc-degradation | succeeded | 4 | 169 |
| ev3-a100-node-03 | power_fault |  | power_fault | power_fault | 0.9 | monitor | rb-013-bmc-redfish-triage, rb-015-power-capping-policy | succeeded | 5 | 242 |
| ev3-h100-node-01 | power_fault |  | power_fault | power_fault | 0.9 | monitor | rb-013-bmc-redfish-triage, rb-015-power-capping-policy | succeeded | 3 | 148 |
| ev3-a100-node-02 | pcie_degradation | yes | pcie_degradation | pcie_degradation | 0.9 | drain_node | rb-006-pcie-degradation | succeeded | 4 | 131 |
| ev3-h100-node-02 | pcie_degradation |  | pcie_degradation | noisy_neighbor ✗ | 0.9 | monitor | rb-007-noisy-neighbor-triage | succeeded | 4 | 105 |
| ev3-h100-node-02 | ecc_degradation |  | ecc_degradation | ecc_degradation | 0.8 | monitor | rb-002-ecc-degradation | succeeded | 4 | 125 |
| ev3-h100-node-02 | ecc_degradation |  | ecc_degradation | ecc_degradation | 0.95 | replace_part | inc-2025036, rb-002-ecc-degradation | succeeded | 5 | 187 |
| ev3-h100-node-04 | ecc_degradation |  | ecc_degradation | ecc_degradation | 0.95 | drain_node | rb-002-ecc-degradation | succeeded | 3 | 98 |
| ev3-a100-node-02 | nvlink_degradation |  | nvlink_degradation | nvlink_degradation | 0.95 | drain_node | rb-005-nvlink-degradation | succeeded | 3 | 88 |
| ev3-h100-node-03 | gpu_off_bus |  | gpu_off_bus | gpu_off_bus | 0.95 | drain_node | rb-004-gpu-off-bus | succeeded | 5 | 120 |
| ev3-h100-node-02 | gpu_off_bus | yes | gpu_off_bus | gpu_off_bus | 0.95 | none | rb-004-gpu-off-bus | succeeded | 5 | 127 |
