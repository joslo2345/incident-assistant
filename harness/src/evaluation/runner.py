"""Runs the agent over an eval set and writes a report tagged with the git commit.

Each case gets a fresh in-memory approvals store, so a drain requested in one run (or round)
can't show up in the next run's view of the node. Traces still go to Postgres, labelled with the
set, the label and the case, so any number in a report can be traced back to its run.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from agent.data import MemoryApprovals
from agent.llm import Provider
from agent.loop import PROMPT_SHA, Investigator
from agent.runtime import Runtime
from agent.tools import Toolbox
from evaluation.cassette import RecordingProvider, ReplayProvider
from evaluation.judge import Judge, diagnosis_text
from evaluation.score import CaseResult, result_dict, score_case, summarize, to_markdown
from evaluation.sets import REPO, EvalCase, composition

log = logging.getLogger("evaluation")
REPORTS = REPO / "eval" / "reports" / "a6"


def load_local_env() -> None:
    """Connection settings for the local stack from deploy/.env (as scripts/agent_env.py)."""
    env_file = REPO / "deploy" / ".env"
    if not env_file.exists():
        return
    env = dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    db = "@localhost:5432/telemetry"
    if "AGENT_DB_PASSWORD" in env:
        os.environ.setdefault(
            "DATABASE_URL", f"postgresql://incident_agent:{env['AGENT_DB_PASSWORD']}{db}"
        )
    os.environ.setdefault("KNOWLEDGE_URL", "http://localhost:8001")
    if "KNOWLEDGE_API_KEY" in env:
        os.environ.setdefault("KNOWLEDGE_API_KEY", env["KNOWLEDGE_API_KEY"])


def git_state() -> tuple[str, bool]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout

    commit = git("rev-parse", "--short", "HEAD").strip() or "unknown"
    # Reports and recordings are outputs of a run, not inputs; ignore them for "dirty".
    dirty = [
        line for line in git("status", "--porcelain").splitlines()
        if not line[3:].startswith(("eval/reports/", "eval/cassettes/"))
    ]  # fmt: skip
    return commit, bool(dirty)


@dataclass
class RunOptions:
    set_name: str
    label: str
    judge: Judge | None = None
    limit: int | None = None


async def _case(rt: Runtime, provider: Provider, case: EvalCase, opts: RunOptions) -> CaseResult:
    incident = await rt.data.incident(uuid.UUID(case.incident_id))
    if incident is None:
        raise SystemExit(
            f"incident {case.incident_id} ({case.case_id}) is not in the database; the eval data "
            "has expired or was rebuilt. Run `make eval-data`."
        )
    if isinstance(provider, RecordingProvider | ReplayProvider):
        provider.begin(case.case_id, incident)
    approvals = MemoryApprovals()
    investigator = Investigator(
        provider, Toolbox(rt.data, rt.toolbox.knowledge, approvals), approvals, rt.traces,
        rt.settings.budget,
        labels={"eval_set": opts.set_name, "eval_label": opts.label, "case_id": case.case_id},
    )  # fmt: skip
    result = await investigator.investigate(incident)
    d, run = result.diagnosis, result.run
    tool_steps = [s for s in result.trace if s.kind == "tool" and s.name != "submit_diagnosis"]
    text_calls = sum(
        str(c.get("id", "")).startswith("text_")
        for s in result.trace if s.kind == "model" and isinstance(s.output, dict)
        for c in s.output.get("tool_calls", [])
    )  # fmt: skip
    r = CaseResult(
        case_id=case.case_id, incident_id=case.incident_id, truth=case.truth.value,
        is_fault=case.is_fault, bmc_dropped=case.bmc_dropped, run_id=str(run.run_id),
        status=run.status, root_cause=d.root_cause.value if d else None,
        confidence=d.confidence if d else None,
        action=d.recommended_action.action.value if d else None,
        cited_chunks=[c.chunk_id for c in d.citations] if d else [],
        summary=d.summary if d else None,
        rationale=d.recommended_action.rationale if d else None,
        steps=run.steps, tool_calls=run.tool_calls,
        tool_errors=sum(s.is_error for s in tool_steps), text_tool_calls=text_calls,
        input_tokens=run.input_tokens, output_tokens=run.output_tokens, cost_usd=run.cost_usd,
        latency_s=(run.latency_ms or 0) / 1000,
    )  # fmt: skip
    r = score_case(case, r)
    if opts.judge and d is not None:
        text = diagnosis_text(r.root_cause or "", r.summary or "", r.action or "",
                              r.rationale or "")  # fmt: skip
        for chunk_id in r.cited_chunks:
            passage = await _chunk_text(rt, chunk_id)
            v = await opts.judge.judge(text, chunk_id, passage)
            r.citation_verdicts.append({"chunk_id": chunk_id, "supported": v.supported,
                                        "reason": v.reason})  # fmt: skip
    return r


async def _chunk_text(rt: Runtime, chunk_id: str) -> str:
    knowledge: Any = rt.toolbox.knowledge
    response = await knowledge.client.get(f"/v1/chunks/{quote(chunk_id, safe='')}")
    response.raise_for_status()
    return str(response.json()["text"])


async def run_set(
    rt: Runtime, provider: Provider, cases: list[EvalCase], opts: RunOptions
) -> dict[str, Any]:
    commit, dirty = git_state()
    started = datetime.now(UTC)
    chosen = cases[: opts.limit] if opts.limit else cases
    results = []
    for i, case in enumerate(chosen, 1):
        t0 = time.perf_counter()
        r = await _case(rt, provider, case, opts)
        results.append(r)
        log.info(
            "[%d/%d] %s %s -> %s %s (%s, %.0fs)", i, len(chosen), case.case_id, case.truth.value,
            r.root_cause, r.action, r.status, time.perf_counter() - t0,
        )  # fmt: skip
    return {
        "meta": {
            "set": opts.set_name,
            "label": opts.label,
            "commit": commit,
            "dirty": dirty,
            "provider": provider.name,
            "model": provider.model,
            "prompt_sha": PROMPT_SHA,
            "judge": opts.judge.model if opts.judge else None,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "composition": composition(chosen),
        },
        "summary": summarize(results),
        "cases": [result_dict(r) for r in results],
    }


def write_report(report: dict[str, Any], out_dir: Path | None = None) -> Path:
    m = report["meta"]
    out_dir = out_dir or REPORTS / m["set"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{m['label']}"
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    stem.with_suffix(".md").write_text(to_markdown(report))
    return stem.with_suffix(".md")
