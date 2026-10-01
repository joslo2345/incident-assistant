"""Generate the Grafana dashboards in deploy/grafana/dashboards/.

Template variables in SQL always use ${var:sqlstring}, which quotes each value and escapes
single quotes. Viewers can set variables through the URL, so raw $var would be SQL injection.

Dashboards are defined here as code and written as JSON that Grafana provisions at startup.
Edit this file, not the JSON:  uv run python scripts/build_dashboards.py
"""

import json
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).resolve().parent.parent / "deploy" / "grafana" / "dashboards"
TSDB = {"type": "grafana-postgresql-datasource", "uid": "timescaledb"}
PROM = {"type": "prometheus", "uid": "prometheus"}

Panel = dict[str, Any]


class Layout:
    """Places panels left to right on Grafana's 24-column grid, wrapping to new rows."""

    def __init__(self) -> None:
        self.x = self.y = self.row_h = 0
        self.next_id = 1

    def place(self, panel: Panel, w: int, h: int) -> Panel:
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        panel["id"] = self.next_id
        self.next_id += 1
        self.x += w
        self.row_h = max(self.row_h, h)
        return panel


def sql(query: str, fmt: str = "time_series", ref: str = "A") -> dict[str, Any]:
    return {
        "datasource": TSDB,
        "refId": ref,
        "rawQuery": True,
        "editorMode": "code",
        "format": fmt,
        "rawSql": " ".join(query.split()),
    }


def promql(expr: str, legend: str, ref: str = "A") -> dict[str, Any]:
    return {"datasource": PROM, "refId": ref, "expr": expr, "legendFormat": legend}


def timeseries(title: str, targets: list[dict[str, Any]], unit: str, **opts: Any) -> Panel:
    return {
        "type": "timeseries",
        "title": title,
        "datasource": targets[0]["datasource"],
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {"lineWidth": 1, "fillOpacity": 0, "showPoints": "never"},
                **opts,
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def stat(
    title: str, target: dict[str, Any], unit: str, thresholds: list[tuple[float | None, str]]
) -> Panel:
    return {
        "type": "stat",
        "title": title,
        "datasource": target["datasource"],
        "targets": [target],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"value": v, "color": c} for v, c in thresholds],
                },
            },
            "overrides": [],
        },
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value"},
    }


def table(title: str, target: dict[str, Any]) -> Panel:
    return {
        "type": "table",
        "title": title,
        "datasource": target["datasource"],
        "targets": [target],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {"showHeader": True},
    }


def dashboard(uid: str, title: str, panels: list[Panel], **extra: Any) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "tags": ["incident-assistant"],
        "timezone": "utc",
        "schemaVersion": 39,
        "refresh": "10s",
        "time": {"from": "now-6h", "to": "now"},
        "panels": panels,
        **extra,
    }


GREEN, YELLOW, RED = "green", "yellow", "red"

# ECC counters are cumulative but reset when the driver reloads. Count only increases between
# consecutive 1-minute buckets (like Prometheus increase()); a drop is a reset, not negative errors.
ECC_INCREASE = (
    "SELECT bucket, node_id, gpu_index, gpu_model, "
    "greatest(ecc_sbe_total - lag(ecc_sbe_total) OVER w, 0) AS new_sbe, "
    "greatest(ecc_dbe_total - lag(ecc_dbe_total) OVER w, 0) AS new_dbe "
    "FROM gpu_metrics_1m WHERE $__timeFilter(bucket) AND node_id IN (${node:sqlstring}) "
    "WINDOW w AS (PARTITION BY node_id, gpu_index ORDER BY bucket)"
)


def fleet_health() -> dict[str, Any]:
    lay = Layout()
    node_filter = "node_id IN (${node:sqlstring})"
    panels = [
        lay.place(
            table(
                "Incidents (live detector)",
                sql(
                    "SELECT opened_at AS opened, status, severity, suspected_failure_type AS type, "
                    "node_id AS node, array_to_string(gpu_indices, ',') AS gpus, title "
                    "FROM incidents WHERE detector_run = 'live' AND $__timeFilter(opened_at) "
                    f"AND {node_filter} ORDER BY opened_at DESC LIMIT 50",
                    "table",
                ),
            ),
            24,
            7,
        ),
        lay.place(
            stat(
                "GPUs reporting (last 5 min)",
                sql(
                    "SELECT count(DISTINCT (node_id, gpu_index)) FROM gpu_metrics "
                    "WHERE time > $__timeTo()::timestamptz - INTERVAL '5 minutes' "
                    "AND time <= $__timeTo()::timestamptz",
                    "table",
                ),
                "none",
                [(None, RED), (63, YELLOW), (64, GREEN)],
            ),
            4,
            4,
        ),
        lay.place(
            stat(
                "Fleet utilization",
                sql(
                    f"SELECT avg(util_avg) FROM gpu_metrics_1m WHERE $__timeFilter(bucket) AND {node_filter}",
                    "table",
                ),
                "percent",
                [(None, GREEN)],
            ),
            4,
            4,
        ),
        lay.place(
            stat(
                "Hottest GPU",
                sql(
                    f"SELECT max(temp_max) FROM gpu_metrics_1m WHERE $__timeFilter(bucket) AND {node_filter}",
                    "table",
                ),
                "celsius",
                [(None, GREEN), (80, YELLOW), (87, RED)],
            ),
            4,
            4,
        ),
        lay.place(
            stat(
                "Time throttled",
                sql(
                    "SELECT 100.0 * sum(throttled_samples) / nullif(sum(samples), 0) FROM gpu_metrics_1m "
                    f"WHERE $__timeFilter(bucket) AND {node_filter}",
                    "table",
                ),
                "percent",
                [(None, GREEN), (20, YELLOW), (50, RED)],
            ),
            4,
            4,
        ),
        lay.place(
            stat(
                "New correctable ECC errors",
                sql(
                    f"SELECT coalesce(sum(new_sbe), 0) FROM ({ECC_INCREASE}) per_bucket",
                    "table",
                ),
                "none",
                [(None, GREEN), (10, YELLOW), (100, RED)],
            ),
            4,
            4,
        ),
        lay.place(
            stat(
                "BMC warnings + XIDs",
                sql(
                    "SELECT (SELECT count(*) FROM bmc_log WHERE $__timeFilter(time) AND severity <> 'ok' "
                    f"AND {node_filter}) + (SELECT count(*) FROM xid_events WHERE $__timeFilter(time) "
                    f"AND {node_filter})",
                    "table",
                ),
                "none",
                [(None, GREEN), (1, YELLOW), (10, RED)],
            ),
            4,
            4,
        ),
        lay.place(
            timeseries(
                "Hottest GPU per node",
                [
                    sql(
                        "SELECT $__timeGroupAlias(bucket, $__interval), node_id AS metric, max(temp_max) AS value "
                        f"FROM gpu_metrics_1m WHERE $__timeFilter(bucket) AND {node_filter} GROUP BY 1, 2 ORDER BY 1"
                    )
                ],
                "celsius",
            ),
            12,
            8,
        ),
        lay.place(
            timeseries(
                "Power per node",
                [
                    sql(
                        "SELECT time, node_id AS metric, sum(p) AS value FROM ("
                        "SELECT $__timeGroupAlias(bucket, $__interval), node_id, gpu_index, avg(power_avg) AS p "
                        f"FROM gpu_metrics_1m WHERE $__timeFilter(bucket) AND {node_filter} GROUP BY 1, 2, 3"
                        ") per_gpu GROUP BY 1, 2 ORDER BY 1"
                    )
                ],
                "watt",
            ),
            12,
            8,
        ),
        lay.place(
            timeseries(
                "Utilization per node",
                [
                    sql(
                        "SELECT $__timeGroupAlias(bucket, $__interval), node_id AS metric, avg(util_avg) AS value "
                        f"FROM gpu_metrics_1m WHERE $__timeFilter(bucket) AND {node_filter} GROUP BY 1, 2 ORDER BY 1"
                    )
                ],
                "percent",
                min=0,
                max=100,
            ),
            12,
            8,
        ),
        lay.place(
            timeseries(
                "GPU temperature, $gpu_node",
                [
                    sql(
                        "SELECT $__timeGroupAlias(bucket, $__interval), 'GPU ' || gpu_index AS metric, "
                        "max(temp_max) AS value FROM gpu_metrics_1m "
                        "WHERE $__timeFilter(bucket) AND node_id = ${gpu_node:sqlstring} GROUP BY 1, 2 ORDER BY 1"
                    )
                ],
                "celsius",
            ),
            12,
            8,
        ),
        lay.place(
            table(
                "GPUs with new correctable ECC errors",
                sql(
                    "SELECT node_id AS node, gpu_index AS gpu, last(gpu_model, bucket) AS model, "
                    "sum(new_sbe) AS new_errors, sum(new_dbe) AS new_uncorrectable "
                    f"FROM ({ECC_INCREASE}) per_bucket GROUP BY 1, 2 "
                    "HAVING sum(new_sbe) > 0 OR sum(new_dbe) > 0 "
                    "ORDER BY new_uncorrectable DESC, new_errors DESC LIMIT 20",
                    "table",
                ),
            ),
            10,
            9,
        ),
        lay.place(
            table(
                "Recent BMC warnings and XID errors",
                sql(
                    "SELECT time, node_id AS node, NULL::smallint AS gpu, severity, entry_code AS code, message "
                    f"FROM bmc_log WHERE $__timeFilter(time) AND severity <> 'ok' AND {node_filter} "
                    "UNION ALL SELECT time, node_id, gpu_index, 'xid', xid_code::text, message "
                    f"FROM xid_events WHERE $__timeFilter(time) AND {node_filter} "
                    "ORDER BY time DESC LIMIT 50",
                    "table",
                ),
            ),
            14,
            9,
        ),
    ]
    node_query = "SELECT DISTINCT node_id FROM gpu_metrics_1m WHERE bucket > now() - INTERVAL '30 days' ORDER BY 1"
    templating = {
        "list": [
            {
                "name": "node",
                "label": "Nodes",
                "type": "query",
                "datasource": TSDB,
                "query": node_query,
                "definition": node_query,
                "multi": True,
                "includeAll": True,
                "current": {"text": "All", "value": "$__all"},
                "refresh": 2,
            },
            {
                "name": "gpu_node",
                "label": "Node (per-GPU panel)",
                "type": "query",
                "datasource": TSDB,
                "query": node_query,
                "definition": node_query,
                "multi": False,
                "includeAll": False,
                "refresh": 2,
            },
        ]
    }
    return dashboard("fleet-health", "Fleet health", panels, templating=templating)


def system_health() -> dict[str, Any]:
    lay = Layout()
    telemetry = 'path="/v1/telemetry"'

    def quantiles(metric: str, selector: str = "") -> list[dict[str, Any]]:
        sel = f"{{{selector}}}" if selector else ""
        return [
            promql(
                f"histogram_quantile({q}, sum by (le) (rate({metric}_bucket{sel}[1m])))",
                f"p{int(q * 100)}",
                ref,
            )
            for q, ref in ((0.5, "A"), (0.95, "B"), (0.99, "C"))
        ]

    panels = [
        lay.place(
            timeseries(
                "Ingest requests by status",
                [
                    promql(
                        f"sum by (status) (rate(ingest_requests_total{{{telemetry}}}[1m]))",
                        "{{status}}",
                    )
                ],
                "reqps",
            ),
            8,
            8,
        ),
        lay.place(
            timeseries(
                "Ingest events",
                [promql("sum by (result) (rate(ingest_events_total[1m]))", "{{result}}")],
                "ops",
            ),
            8,
            8,
        ),
        lay.place(
            timeseries(
                "Ingest request latency", quantiles("ingest_request_seconds", telemetry), "s"
            ),
            8,
            8,
        ),
        lay.place(
            timeseries("Kafka publish latency (ack)", quantiles("ingest_publish_seconds"), "s"),
            8,
            8,
        ),
        lay.place(
            timeseries(
                "Events in flight", [promql("ingest_in_flight_events", "in flight")], "none"
            ),
            8,
            8,
        ),
        lay.place(
            timeseries(
                "Consumer lag", [promql("sum(consumer_lag_messages)", "messages behind")], "none"
            ),
            8,
            8,
        ),
        lay.place(
            timeseries(
                "Rows written",
                [
                    promql(
                        "sum by (table, result) (rate(consumer_rows_total[1m]))",
                        "{{table}} {{result}}",
                    )
                ],
                "ops",
            ),
            8,
            8,
        ),
        lay.place(
            timeseries("DB batch write time", quantiles("consumer_write_seconds"), "s"), 8, 8
        ),
        lay.place(
            timeseries(
                "End-to-end latency (publish -> DB commit)",
                quantiles("consumer_end_to_end_seconds"),
                "s",
            ),
            8,
            8,
        ),
        lay.place(
            timeseries(
                "Errors",
                [
                    promql(
                        'sum(rate(consumer_messages_total{result="invalid"}[1m]))',
                        "sent to DLQ",
                        "A",
                    ),
                    promql("sum(rate(consumer_db_errors_total[1m]))", "DB write retries", "B"),
                    promql(
                        f'sum(rate(ingest_requests_total{{{telemetry},status=~"4..|5.."}}[1m]))',
                        "ingest 4xx/5xx",
                        "C",
                    ),
                ],
                "ops",
            ),
            12,
            8,
        ),
        lay.place(
            timeseries("Consumer batch size", quantiles("consumer_batch_messages"), "none"), 12, 8
        ),
    ]
    return dashboard(
        "system-health", "System health", panels, time={"from": "now-30m", "to": "now"}
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, build in (("fleet-health", fleet_health), ("system-health", system_health)):
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(build(), indent=2) + "\n")
        print(f"wrote {path.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
