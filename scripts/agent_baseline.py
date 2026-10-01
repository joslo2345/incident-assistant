"""Score the agent's latest diagnosis for each incident of a detector run against the injected
ground truth. A first baseline for A5; A6 replaces it with the full evaluation harness.

    uv run python scripts/agent_baseline.py --run ev3-v3-residual \\
        --ground-truth eval/runs/ev3/ground_truth.jsonl --out eval/reports/agent-baseline

Incidents are matched to faults the same way the detector is scored (detector.score.match).
Investigate first with `agent investigate --detector-run <run>`.
"""

import argparse
import asyncio
import json
import os
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

import asyncpg

from detector.score import load_found, load_truth, match

REPO = Path(__file__).resolve().parent.parent


def database_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    env = dict(
        line.split("=", 1)
        for line in (REPO / "deploy" / ".env").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    return f"postgresql://incident_agent:{env['AGENT_DB_PASSWORD']}@localhost:5432/telemetry"


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


async def runs_for(url: str, incident_ids: list[str]) -> dict[str, dict[str, Any]]:
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT ON (r.incident_id) r.incident_id::text, r.run_id, r.status, r.steps, "
            "r.tool_calls, r.input_tokens, r.output_tokens, r.cost_usd::float8 AS cost_usd, "
            "r.latency_ms, r.diagnosis::text, r.model, "
            "(SELECT count(*) FROM agent_steps s WHERE s.run_id = r.run_id "
            " AND s.name = 'submit_diagnosis' AND s.is_error) AS rejected_submissions, "
            "(SELECT count(*) FROM agent_steps s WHERE s.run_id = r.run_id "
            " AND s.kind = 'tool' AND s.name <> 'submit_diagnosis' AND s.is_error) AS tool_errors, "
            "(SELECT count(*) FROM agent_steps s, jsonb_array_elements(s.output->'tool_calls') c "
            " WHERE s.run_id = r.run_id AND c->>'id' LIKE 'text\\_%') AS text_tool_calls "
            "FROM agent_runs r WHERE r.incident_id = ANY($1::uuid[]) AND r.finished_at IS NOT NULL "
            "ORDER BY r.incident_id, r.started_at DESC",
            incident_ids,
        )
    finally:
        await conn.close()
    out = {}
    for r in rows:
        d = dict(r)
        d["diagnosis"] = json.loads(d["diagnosis"]) if d["diagnosis"] else None
        out[d["incident_id"]] = d
    return out


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    url = database_url()
    truths = load_truth(Path(args.ground_truth))
    found = [f for f in await load_found(url, args.run) if f.severity != "sev4"]
    matched = match(truths, found)
    runs = await runs_for(url, [f.incident_id for f in found])

    rows = []
    for f in sorted(found, key=lambda f: f.opened_at):
        truth, run = matched[f.incident_id], runs.get(f.incident_id)
        if run is None:
            continue
        d = run["diagnosis"]
        cited_docs = sorted({c["doc_id"] for c in d["citations"]}) if d else []
        rows.append({
            "incident_id": f.incident_id, "node": f.node_id,
            "truth": truth.type if truth else None,
            "bmc_dropped": truth.bmc_dropped if truth else None,
            "detector": f.type, "agent": d["root_cause"] if d else None,
            "confidence": d["confidence"] if d else None,
            "action": d["recommended_action"]["action"] if d else None,
            "cited_docs": cited_docs,
            "cites_type_runbook": truth is not None and any(
                doc.startswith("rb-") and doc.endswith(truth.type.replace("_", "-"))
                for doc in cited_docs
            ),
            **{k: run[k] for k in ("status", "steps", "tool_calls", "input_tokens",
                                   "output_tokens", "cost_usd", "latency_ms",
                                   "rejected_submissions", "tool_errors", "text_tool_calls",
                                   "model")},
        })  # fmt: skip

    scored = [r for r in rows if r["truth"]]
    ok = [r for r in rows if r["status"] == "succeeded"]
    lat = [r["latency_ms"] / 1000 for r in rows if r["latency_ms"] is not None]

    def accuracy(subset: list[dict[str, Any]], key: str) -> str:
        return f"{sum(r[key] == r['truth'] for r in subset)}/{len(subset)}" if subset else "-"

    summary = {
        "detector_run": args.run,
        "incidents_investigated": len(rows),
        "matched_to_a_fault": len(scored),
        "status": dict(Counter(r["status"] for r in rows)),
        "root_cause_agent": accuracy(scored, "agent"),
        "root_cause_detector_rules": accuracy(scored, "detector"),
        "root_cause_agent_bmc_dropped": accuracy([r for r in scored if r["bmc_dropped"]], "agent"),
        "cites_runbook_for_true_type": f"{sum(r['cites_type_runbook'] for r in scored)}/"
        f"{len(scored)}",
        "actions": dict(Counter(r["action"] for r in ok)),
        "latency_s_p50": round(statistics.median(lat)) if lat else None,
        "latency_s_p95": round(pct(lat, 0.95)) if lat else None,
        "steps_mean": round(statistics.mean(r["steps"] for r in rows), 1) if rows else None,
        "tool_calls_mean": round(statistics.mean(r["tool_calls"] for r in rows), 1)
        if rows
        else None,
        "tokens_mean": round(statistics.mean(r["input_tokens"] + r["output_tokens"] for r in rows))
        if rows
        else None,
        "cost_usd_total": round(sum(r["cost_usd"] for r in rows), 4),
        "rejected_submissions": sum(r["rejected_submissions"] for r in rows),
        "tool_errors": sum(r["tool_errors"] for r in rows),
        "tool_calls_recovered_from_text": sum(r["text_tool_calls"] for r in rows),
        "model": rows[0]["model"] if rows else None,
    }
    return {"summary": summary, "incidents": rows}


def to_markdown(result: dict[str, Any]) -> str:
    s = result["summary"]
    lines = [
        f"# Agent baseline: `{s['detector_run']}`",
        "",
        f"Model `{s['model']}`. {s['incidents_investigated']} actionable incidents investigated, "
        f"{s['matched_to_a_fault']} matched to an injected fault.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in s.items()
              if k not in {"detector_run", "model"}]  # fmt: skip
    lines += [
        "",
        "| Node | Truth | BMC dropped | Detector | Agent | Conf. | Action | Cited | Status | "
        "Steps | Latency (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in result["incidents"]:
        mark = "" if r["agent"] == r["truth"] else " ✗"
        lines.append(
            f"| {r['node']} | {r['truth']} | {'yes' if r['bmc_dropped'] else ''} | {r['detector']} "
            f"| {r['agent']}{mark} | {r['confidence']} | {r['action']} | "
            f"{', '.join(r['cited_docs'])} | {r['status']} | {r['steps']} | "
            f"{'-' if r['latency_ms'] is None else round(r['latency_ms'] / 1000)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--run", required=True, help="detector run, e.g. ev3-v3-residual")
    p.add_argument("--ground-truth", required=True)
    p.add_argument("--out", help="write <out>.md and <out>.json")
    args = p.parse_args()
    result = asyncio.run(main_async(args))
    md = to_markdown(result)
    if args.out:
        Path(f"{args.out}.md").write_text(md)
        Path(f"{args.out}.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(md)


if __name__ == "__main__":
    main()
