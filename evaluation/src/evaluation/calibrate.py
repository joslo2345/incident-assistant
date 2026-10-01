"""How far to trust the judge: agreement with hand grades on the same (case, citation) pairs.

Hand grades live in eval/judge/hand_grades.jsonl, one per line:
    {"case_id": "...", "chunk_id": "...", "supported": true, "note": "..."}
They are graded from the diagnosis and the passage alone, before looking at the judge's verdict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HAND_GRADES = Path(__file__).resolve().parents[3] / "eval" / "judge" / "hand_grades.jsonl"


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(a == b for a, b in pairs) / n
    p_a = sum(a for a, _ in pairs) / n
    p_b = sum(b for _, b in pairs) / n
    expected = p_a * p_b + (1 - p_a) * (1 - p_b)
    return 1.0 if expected == 1 else round((observed - expected) / (1 - expected), 3)


def calibrate(report: dict[str, Any], grades_path: Path = HAND_GRADES) -> dict[str, Any]:
    judged = {
        (c["case_id"], v["chunk_id"]): v["supported"]
        for c in report["cases"]
        for v in c["citation_verdicts"]
    }
    hand = [json.loads(line) for line in grades_path.read_text().splitlines() if line.strip()]
    pairs, missing = [], []
    for g in hand:
        key = (g["case_id"], g["chunk_id"])
        if key in judged:
            pairs.append((bool(g["supported"]), judged[key]))
        else:
            missing.append(key)
    tp = sum(h and j for h, j in pairs)
    tn = sum(not h and not j for h, j in pairs)
    fp = sum(not h and j for h, j in pairs)  # judge says supported, hand says not: lenient
    fn = sum(h and not j for h, j in pairs)  # judge says unsupported, hand says supported: strict
    return {
        "pairs": len(pairs),
        "incidents": len({g["case_id"] for g in hand}),
        "agreement": round((tp + tn) / len(pairs), 3) if pairs else None,
        "cohen_kappa": cohen_kappa(pairs),
        "confusion": {
            "both_supported": tp,
            "both_unsupported": tn,
            "judge_lenient": fp,
            "judge_strict": fn,
        },
        "hand_supported_rate": round(sum(h for h, _ in pairs) / len(pairs), 3) if pairs else None,
        "judge_supported_rate": round(sum(j for _, j in pairs) / len(pairs), 3) if pairs else None,
        "missing_from_report": [list(k) for k in missing],
    }
