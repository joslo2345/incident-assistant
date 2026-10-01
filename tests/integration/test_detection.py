"""End-to-end detection quality gate: replay a day with one fault of each type through the
stack, run the detector, and check the scores. Seeded, so the injected faults are fixed."""

import json
import secrets
import subprocess

from .conftest import REPO


def test_detector_finds_injected_faults_without_false_alarms() -> None:
    run_id = f"it-{secrets.token_hex(3)}"  # fresh node names: never mixes with other data
    subprocess.run(
        ["uv", "run", "python", "scripts/eval_detection.py", "--run-id", run_id,
         "--duration", "1d", "--faults", "7", "--seed", "5"],
        cwd=REPO, check=True, capture_output=True, text=True,
    )  # fmt: skip
    results = json.loads((REPO / "eval" / "runs" / run_id / "results.json").read_text())
    full = results["v3-residual"]
    print(json.dumps({k: v["overall"] for k, v in results.items()}, indent=2))

    assert full["overall"]["faults"] == 6  # plus one noisy-neighbor decoy
    assert full["overall"]["recall"] >= 5 / 6
    assert full["overall"]["precision"] >= 0.9
    assert full["decoys"]["raised_alarm"] == 0
    assert full["overall"]["classified_correctly"] == full["overall"]["detected"]
    # The residual layer must not be worse than static rules alone.
    assert full["overall"]["detected"] >= results["v1-static"]["overall"]["detected"]
