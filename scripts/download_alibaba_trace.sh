#!/usr/bin/env bash
# Download the Alibaba cluster-trace-gpu-v2020 tables the replayer needs into data/alibaba/.
# Source: https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2020
set -euo pipefail

DEST="$(cd "$(dirname "$0")/.." && pwd)/data/alibaba"
BASE="https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2020GPUTraces"
HEADERS="https://raw.githubusercontent.com/alibaba/clusterdata/master/cluster-trace-gpu-v2020/data"

# Checksums from the dataset README.
SUMS="cc0d38a4045af1b1af8179de8b1b54b1ddd995e6160d6d061a6b1000f1276c2d  pai_machine_spec.tar.gz
1bf1e423a7ce3f8d086699801c362fd56a7182abdb234139e5ebbed97995ca06  pai_instance_table.tar.gz
9a0b82e8bdf3949281e4ba1423d9b4b34847e52799eecb138966de46da69c7a0  pai_sensor_table.tar.gz"

mkdir -p "$DEST" && cd "$DEST"
for t in pai_machine_spec pai_instance_table pai_sensor_table; do
  if [ -f "$t.csv" ]; then echo "$t.csv exists, skipping"; continue; fi
  curl -fSL --retry 3 -C - -o "$t.tar.gz" "$BASE/$t.tar.gz"
  curl -fsSL -o "$t.header" "$HEADERS/$t.header"
  grep " $t.tar.gz\$" <<<"$SUMS" | shasum -a 256 -c -
  tar -xzf "$t.tar.gz" && rm "$t.tar.gz"
done
ls -lh
