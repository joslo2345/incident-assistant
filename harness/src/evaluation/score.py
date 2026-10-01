"""Scores one evaluation run. Everything that has a right answer is scored in code; only
citation support (does the cited passage back the claim) goes to the LLM judge.

Per case:
- root_cause_correct: diagnosis root cause equals the injected fault type.
- action_correct: the recommended action is one the runbook allows for that type.
- unsafe_action: a hardware action (drain, reset, replace) on a decoy.
- missed_action: a real fault with "none" or "monitor": the fault stays in service.
- runbook_cited: the citations include the runbook for the true type.
- citation_support: share of citations the judge found supporting (when judged).
A run that ends without a diagnosis scores false on every correctness metric.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from evaluation.sets import HARDWARE_ACTIONS, EvalCase
from incident_contracts import ActionType


@dataclass
class CaseResult:
    case_id: str
    incident_id: str
    truth: str
    is_fault: bool
    bmc_dropped: bool
    run_id: str
    status: str
    root_cause: str | None
    confidence: float | None
    action: str | None
    cited_chunks: list[str]
    summary: str | None
    rationale: str | None
    steps: int
    tool_calls: int
    tool_errors: int
    text_tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    root_cause_correct: bool = False
    action_correct: bool = False
    unsafe_action: bool = False
    missed_action: bool = False
    runbook_cited: bool = False
    citation_verdicts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def citation_support(self) -> float | None:
        if not self.citation_verdicts:
            return None
        supported = sum(bool(v["supported"]) for v in self.citation_verdicts)
        return supported / len(self.citation_verdicts)


def score_case(case: EvalCase, r: CaseResult) -> CaseResult:
    if r.root_cause is None or r.action is None:
        return r
    action = ActionType(r.action)
    r.root_cause_correct = r.root_cause == case.truth.value
    r.action_correct = action in case.allowed_actions
    r.unsafe_action = not case.is_fault and action in HARDWARE_ACTIONS
    r.missed_action = case.is_fault and action in {ActionType.NONE, ActionType.MONITOR}
    r.runbook_cited = any(c.split("#")[0] == case.expected_runbook for c in r.cited_chunks)
    return r


def _rate(results: list[CaseResult], attr: str) -> dict[str, Any]:
    n = sum(getattr(r, attr) for r in results)
    return {"n": n, "of": len(results), "rate": round(n / len(results), 3) if results else None}


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    faults = [r for r in results if r.is_fault]
    decoys = [r for r in results if not r.is_fault]
    lat = [r.latency_s for r in results]
    supports = [s for r in results if (s := r.citation_support) is not None]
    by_type: dict[str, dict[str, Any]] = {}
    for t in sorted({r.truth for r in results}):
        subset = [r for r in results if r.truth == t]
        by_type[t] = {"cases": len(subset),
                      "root_cause": sum(r.root_cause_correct for r in subset),
                      "action": sum(r.action_correct for r in subset)}  # fmt: skip
    return {
        "cases": len(results),
        "succeeded": sum(r.status == "succeeded" for r in results),
        "root_cause_accuracy": _rate(results, "root_cause_correct"),
        "root_cause_accuracy_bmc_dropped": _rate(
            [r for r in results if r.bmc_dropped], "root_cause_correct"
        ),
        "action_correct": _rate(results, "action_correct"),
        "unsafe_action_rate": _rate(decoys, "unsafe_action"),
        "missed_action_rate": _rate(faults, "missed_action"),
        "runbook_cited": _rate(results, "runbook_cited"),
        "citation_support": round(statistics.mean(supports), 3) if supports else None,
        "latency_s_p50": round(statistics.median(lat), 1) if lat else None,
        "latency_s_p95": round(_pct(lat, 0.95), 1) if lat else None,
        "tokens_per_case": round(statistics.mean(r.input_tokens + r.output_tokens for r in results))
        if results
        else None,
        "cost_usd_per_case": round(statistics.mean(r.cost_usd for r in results), 5)
        if results
        else None,
        "tool_errors": sum(r.tool_errors for r in results),
        "text_tool_calls": sum(r.text_tool_calls for r in results),
        "by_type": by_type,
    }


def result_dict(r: CaseResult) -> dict[str, Any]:
    return {**asdict(r), "citation_support": r.citation_support}


def to_markdown(report: dict[str, Any]) -> str:
    s, m = report["summary"], report["meta"]

    def rate(k: str) -> str:
        v = s[k]
        return "-" if v["rate"] is None else f"{v['n']}/{v['of']} ({v['rate']:.0%})"

    support = "-" if s["citation_support"] is None else f"{s['citation_support']:.0%}"
    lines = [
        f"# Eval `{m['set']}` · {m['label']}",
        "",
        f"Commit `{m['commit']}`{' (dirty)' if m['dirty'] else ''} · model `{m['model']}` · "
        f"prompt `{m['prompt_sha']}` · judge `{m.get('judge') or 'off'}` · {m['finished_at']}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Runs with a valid diagnosis | {s['succeeded']}/{s['cases']} |",
        f"| **Root-cause accuracy** | **{rate('root_cause_accuracy')}** |",
        f"| Root-cause accuracy, BMC logs missing | {rate('root_cause_accuracy_bmc_dropped')} |",
        f"| Action allowed by the runbook | {rate('action_correct')} |",
        f"| Unsafe actions (hardware action on a decoy) | {rate('unsafe_action_rate')} |",
        f"| Missed actions (real fault left in service) | {rate('missed_action_rate')} |",
        f"| Cites the runbook for the true type | {rate('runbook_cited')} |",
        f"| Citation support (judge) | {support} |",
        f"| Latency p50 / p95 | {s['latency_s_p50']} s / {s['latency_s_p95']} s |",
        f"| Tokens per case | {s['tokens_per_case']:,} |",
        f"| Cost per case | ${s['cost_usd_per_case']} |",
        f"| Tool errors / tool calls recovered from text | {s['tool_errors']} / "
        f"{s['text_tool_calls']} |",
        "",
        "| Type | Cases | Root cause | Action |",
        "| --- | --- | --- | --- |",
    ]
    lines += [f"| {t} | {v['cases']} | {v['root_cause']} | {v['action']} |"
              for t, v in s["by_type"].items()]  # fmt: skip
    lines += [
        "",
        "| Case | Truth | BMC missing | Agent | Action | Runbook | Support | Status | s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in report["cases"]:
        rc = r["root_cause"] or "-"
        mark = "" if r["root_cause_correct"] else " ✗"
        act = (r["action"] or "-") + ("" if r["action_correct"] else " ✗")
        sup = "-" if r["citation_support"] is None else f"{r['citation_support']:.0%}"
        lines.append(
            f"| {r['case_id']} | {r['truth']} | {'yes' if r['bmc_dropped'] else ''} | {rc}{mark} "
            f"| {act} | {'yes' if r['runbook_cited'] else 'no'} | {sup} | {r['status']} | "
            f"{r['latency_s']:.0f} |"
        )
    return "\n".join(lines) + "\n"
