"""Evaluation CLI (needs the local stack; `make up`).

evaluate build --name dev --run-id a6dev     # label the incidents of a replayed run
evaluate run --set dev --label baseline --judge
make eval-ci-record     # record the model's replies on a fresh replay of the CI faults
make eval-ci            # replay them through the stack under a fresh run id, gated
evaluate calibrate eval/reports/a6/dev/baseline.json       # judge vs hand grades
evaluate compare eval/reports/a6/dev/baseline.json eval/reports/a6/dev/round-1.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from evaluation import sets

GATED = ("root_cause_accuracy", "action_correct", "runbook_cited")


def _load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())  # type: ignore[no-any-return]


async def _build(args: argparse.Namespace) -> int:
    import os

    from detector.score import load_found, load_truth
    from evaluation.runner import load_local_env

    load_local_env()
    cases: list[sets.EvalCase] = []
    missed: list[str] = []
    runs = []
    for run_id in args.run_id:
        run_dir = sets.REPO / "eval" / "runs" / run_id
        detector_run = f"{run_id}-v3-residual"
        truths = load_truth(run_dir / "ground_truth.jsonl")
        built = sets.build(truths, await load_found(os.environ["DATABASE_URL"], detector_run),
                           detector_run)  # fmt: skip
        cases += built.cases
        missed += built.not_detected
        runs.append({**json.loads((run_dir / "meta.json").read_text()),
                     "detector_run": detector_run})  # fmt: skip
    if args.max_decoys is not None:
        decoys = [c for c in cases if not c.is_fault][: args.max_decoys]
        cases = [c for c in cases if c.is_fault] + decoys
    path = sets.save(args.name, sets.BuiltSet(cases, missed), {"runs": runs})
    print(json.dumps(sets.composition(cases)))
    print(f"wrote {path.relative_to(sets.REPO)}; not detected: {missed or 'none'}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    from agent.llm import provider_from_env
    from agent.runtime import Settings, open_runtime
    from evaluation.cassette import Cassette, RecordingProvider, ReplayProvider
    from evaluation.judge import Judge
    from evaluation.runner import RunOptions, load_local_env, run_set, write_report

    load_local_env()
    cases = sets.load(args.set)
    provider: Any
    cassette = None
    if args.replay:
        provider = ReplayProvider(Cassette.load(Path(args.replay)))
    else:
        provider = provider_from_env()
        if args.record:
            cassette = Cassette(recorded_with={"model": provider.model})
            provider = RecordingProvider(provider, cassette)
    judge = Judge(args.judge_model, prompt=args.judge_prompt) if args.judge else None
    label = args.label or ("replay" if args.replay else None)
    async with open_runtime(Settings.from_env(), provider) as rt:
        from evaluation.runner import git_state

        opts = RunOptions(args.set, label or git_state()[0], judge, args.limit)
        report = await run_set(rt, provider, cases, opts)
    out = write_report(report, Path(args.out) if args.out else None)
    print(out.read_text())
    if cassette is not None:
        cassette.recorded_with.update({"commit": report["meta"]["commit"], "set": args.set})
        cassette.save(Path(args.record))
        expected = Path(args.record).with_suffix(".expected.json")
        expected.write_text(json.dumps(_gate_values(report["summary"]), indent=2) + "\n")
        print(f"recorded {len(cassette.cases)} cases to {args.record}; gate values in {expected}")
    if args.gate:
        return _check_gate(report["summary"], _load(args.gate))
    return 0


def _gate_values(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "cases": summary["cases"],
        "succeeded": summary["succeeded"],
        "tool_errors": summary["tool_errors"],
        **{k: summary[k]["n"] for k in GATED},
    }


def _check_gate(summary: dict[str, Any], expected: dict[str, Any]) -> int:
    got = _gate_values(summary)
    problems = []
    if got["cases"] != expected["cases"]:
        problems.append(f"cases: {got['cases']} != {expected['cases']}")
    for k in ("succeeded", *GATED):
        if got[k] < expected[k]:
            problems.append(f"{k}: {got[k]} < recorded {expected[k]}")
    if got["tool_errors"] > expected["tool_errors"]:
        problems.append(f"tool_errors: {got['tool_errors']} > recorded {expected['tool_errors']}")
    if problems:
        print("EVAL GATE FAILED:\n- " + "\n- ".join(problems))
        return 1
    print(f"eval gate passed: {got}")
    return 0


async def _rejudge(args: argparse.Namespace) -> int:
    """Re-run only the judge over a report's citations (no agent runs) with another prompt."""
    from urllib.parse import quote

    import httpx2

    from evaluation.judge import Judge, diagnosis_text
    from evaluation.runner import load_local_env

    load_local_env()
    import os

    report = _load(args.report)
    judge = Judge(args.judge_model, prompt=args.prompt)
    async with httpx2.AsyncClient(
        base_url=os.environ["KNOWLEDGE_URL"],
        headers={"X-API-Key": os.environ["KNOWLEDGE_API_KEY"]}, timeout=15,
    ) as client:  # fmt: skip
        for c in report["cases"]:
            if not c["root_cause"]:
                continue
            text = diagnosis_text(c["root_cause"], c["summary"], c["action"], c["rationale"])
            verdicts = []
            for chunk_id in c["cited_chunks"]:
                r = await client.get(f"/v1/chunks/{quote(chunk_id, safe='')}")
                r.raise_for_status()
                v = await judge.judge(text, chunk_id, r.json()["text"])
                verdicts.append({"chunk_id": chunk_id, "supported": v.supported,
                                 "reason": v.reason})  # fmt: skip
            c["citation_verdicts"] = verdicts
            n = len(verdicts)
            c["citation_support"] = sum(v["supported"] for v in verdicts) / n if n else None
    supports = [c["citation_support"] for c in report["cases"] if c["citation_support"] is not None]
    report["summary"]["citation_support"] = round(sum(supports) / len(supports), 3)
    report["meta"]["judge"] = f"{args.judge_model} (prompt {args.prompt})"
    Path(args.out).write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"citation support {report['summary']['citation_support']:.0%}; wrote {args.out}")
    return 0


def _calibrate(args: argparse.Namespace) -> int:
    from evaluation.calibrate import calibrate

    result = calibrate(_load(args.report), incidents=args.incidents)
    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    return 0


def _compare(args: argparse.Namespace) -> int:
    reports = [_load(p) for p in args.reports]
    keys = [
        ("root_cause_accuracy", "Root cause"), ("root_cause_accuracy_bmc_dropped", "  BMC missing"),
        ("action_correct", "Action allowed"), ("unsafe_action_rate", "Unsafe actions"),
        ("missed_action_rate", "Missed actions"), ("runbook_cited", "Right runbook cited"),
    ]  # fmt: skip
    head = " | ".join(r["meta"]["label"] for r in reports)
    lines = [f"| Metric | {head} |", "|---" * (len(reports) + 1) + "|"]
    for k, name in keys:
        cells = [f"{r['summary'][k]['n']}/{r['summary'][k]['of']}" for r in reports]
        lines.append(f"| {name} | {' | '.join(cells)} |")
    for k, name in [("citation_support", "Citation support"), ("latency_s_p50", "Latency p50 (s)"),
                    ("tokens_per_case", "Tokens per case")]:  # fmt: skip
        lines.append(f"| {name} | {' | '.join(str(r['summary'][k]) for r in reports)} |")
    print("\n".join(lines))
    return 0


async def _sheet(args: argparse.Namespace) -> int:
    """Grading sheet for hand grades: each (diagnosis, cited passage) pair of the first N
    diagnosed incidents. The judge's verdicts are deliberately left out, so grading stays blind."""
    from urllib.parse import quote

    import httpx2

    from evaluation.runner import load_local_env

    load_local_env()
    import os

    report = _load(args.report)
    cases = [c for c in report["cases"] if c["root_cause"]][: args.n]
    out = ["# Citation grading sheet", "",
           f"From `{args.report}`. Grade each passage: does it support the diagnosis (its root "
           "cause given the observations, or its recommended action)? Judge verdicts are not "
           "shown.", ""]  # fmt: skip
    async with httpx2.AsyncClient(
        base_url=os.environ["KNOWLEDGE_URL"],
        headers={"X-API-Key": os.environ["KNOWLEDGE_API_KEY"]}, timeout=15,
    ) as client:  # fmt: skip
        for c in cases:
            out += [
                f"## {c['case_id']}", "",
                f"**Root cause:** {c['root_cause']} · **Action:** {c['action']}", "",
                f"**Summary:** {c['summary']}", "",
                f"**Rationale:** {c['rationale']}", "",
            ]  # fmt: skip
            for chunk_id in c["cited_chunks"]:
                r = await client.get(f"/v1/chunks/{quote(chunk_id, safe='')}")
                r.raise_for_status()
                out += [f"### `{chunk_id}`", "", "> " + r.json()["text"].replace("\n", "\n> "), ""]
    Path(args.out).write_text("\n".join(out) + "\n")
    pairs = sum(len(c["cited_chunks"]) for c in cases)
    print(f"wrote {args.out}: {len(cases)} incidents, {pairs} citation pairs")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evaluate", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="Label a replayed run's incidents as an eval set")
    b.add_argument("--name", required=True)
    b.add_argument("--run-id", required=True, action="append", help="repeatable")
    b.add_argument("--max-decoys", type=int, help="keep at most this many decoys")
    r = sub.add_parser("run", help="Run the agent over a set and write a report")
    r.add_argument("--set", required=True)
    r.add_argument("--label", help="report name (default: git commit)")
    r.add_argument("--judge", action="store_true", help="judge citation support")
    r.add_argument("--judge-model", default="gemma3:12b")
    r.add_argument("--judge-prompt", default="v2", choices=["v1", "v2"])
    r.add_argument("--limit", type=int)
    r.add_argument("--out", help="report directory (default eval/reports/a6/<set>)")
    mode = r.add_mutually_exclusive_group()
    mode.add_argument("--record", help="record the model's replies to this file")
    mode.add_argument("--replay", help="replay recorded replies instead of calling a model")
    r.add_argument("--gate", help="fail unless results match these recorded values")
    c = sub.add_parser("calibrate", help="Judge agreement with hand grades")
    c.add_argument("report")
    c.add_argument("--out")
    c.add_argument("--incidents", help="slice of graded incidents, e.g. 0:10 or 10:20")
    rj = sub.add_parser("rejudge", help="Re-run only the judge over a report's citations")
    rj.add_argument("report")
    rj.add_argument("--prompt", default="v2", choices=["v1", "v2"])
    rj.add_argument("--judge-model", default="gemma3:12b")
    rj.add_argument("--out", required=True)
    sh = sub.add_parser("sheet", help="Blind grading sheet (no judge verdicts) for hand grades")
    sh.add_argument("report")
    sh.add_argument("--n", type=int, default=20)
    sh.add_argument("--out", default="eval/judge/grading_sheet.md")
    cmp = sub.add_parser("compare", help="Side-by-side metrics of several reports")
    cmp.add_argument("reports", nargs="+")
    return p


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    for noisy in ("httpx2", "httpx", "openai", "agent.loop"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.command == "build":
        sys.exit(asyncio.run(_build(args)))
    if args.command == "run":
        sys.exit(asyncio.run(_run(args)))
    if args.command == "calibrate":
        sys.exit(_calibrate(args))
    if args.command == "rejudge":
        sys.exit(asyncio.run(_rejudge(args)))
    if args.command == "sheet":
        sys.exit(asyncio.run(_sheet(args)))
    sys.exit(_compare(args))
