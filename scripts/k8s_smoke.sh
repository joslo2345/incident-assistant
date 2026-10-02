#!/usr/bin/env bash
# End-to-end check of an installed cluster (kind or cloud): replay telemetry with injected faults
# through the ingest API, run the detector over it inside the cluster, and list the incidents.
#   NS=ia INGEST=http://localhost:8000 scripts/k8s_smoke.sh
# Then open the web UI, sign in, and investigate one of the incidents.
set -euo pipefail
cd "$(dirname "$0")/.."
NS=${NS:-ia}
INGEST=${INGEST:-http://localhost:8000}
RUN=${RUN:-smoke}
set -a; . deploy/.env; set +a

kubectl -n "$NS" exec deploy/agent -- env AGENT_NEW_USER_PASSWORD="$DEMO_APPROVER_PASSWORD" \
  agent users add alice --role approver --name "Alice (on-call)" > /dev/null
kubectl -n "$NS" exec deploy/agent -- env AGENT_NEW_USER_PASSWORD="$DEMO_VIEWER_PASSWORD" \
  agent users add victor --role viewer --name "Victor (viewer)" > /dev/null
echo "accounts: alice (approver), victor (viewer); passwords in deploy/.env"

START=$(python3 -c "from datetime import datetime,timedelta,UTC; print((datetime.now(UTC)-timedelta(hours=8)).replace(second=0,microsecond=0).isoformat())")
END=$(python3 -c "from datetime import datetime,timedelta; print((datetime.fromisoformat('$START')+timedelta(hours=6)).isoformat())")
ACCEPTED=$(uv run replay --api-url "$INGEST" --api-key "$INGEST_API_KEYS" --start "$START" --duration 6h \
  --speed 0 --faults 3 --seed 5 --run-id "$RUN" --fault-types thermal_runaway,power_fault,gpu_off_bus \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['accepted'])")
echo "replayed $ACCEPTED events with 3 injected faults"

q() { kubectl -n "$NS" exec timescaledb-0 -- psql -U postgres -d telemetry -tAc "$1"; }
until [ "$(q "SELECT (SELECT count(*) FROM gpu_metrics WHERE node_id LIKE '$RUN-%')+(SELECT count(*) FROM xid_events WHERE node_id LIKE '$RUN-%')+(SELECT count(*) FROM bmc_log WHERE node_id LIKE '$RUN-%')")" -ge "$ACCEPTED" ]; do sleep 3; done
until [ "$(q "SELECT count(*) FROM gpu_metrics_1m WHERE node_id LIKE '$RUN-%' AND bucket >= '$END'::timestamptz - interval '10 minutes'")" -gt 0 ]; do sleep 5; done
echo "all events stored; 1-minute rollup up to date"

kubectl -n "$NS" exec deploy/detector -- detect backfill --from "$START" --to "$END" \
  --node-prefix "$RUN-" --run "$RUN-v3-residual" --layers static,zscore,residual > /dev/null
echo "incidents (detector run $RUN-v3-residual):"
q "SELECT '  ' || severity || '  ' || suspected_failure_type || '  ' || node_id FROM incidents WHERE detector_run = '$RUN-v3-residual' AND severity <> 'sev4' ORDER BY opened_at"
