from datetime import timedelta

from detector_testkit import T0

from detector.score import Found, Truth, score, to_markdown


def truth(fid: str, ftype: str, start_min: int, node: str = "n1", gpus: tuple[int, ...] = (0,),
          is_fault: bool = True, scope: str = "gpu") -> Truth:  # fmt: skip
    return Truth(fid, ftype, is_fault, scope, node, gpus, T0 + timedelta(minutes=start_min),
                 T0 + timedelta(minutes=start_min + 30))  # fmt: skip


def found(iid: str, ftype: str, first_min: int, node: str = "n1", gpus: tuple[int, ...] = (0,),
          opened_min: int | None = None) -> Found:  # fmt: skip
    t = T0 + timedelta(minutes=first_min)
    opened = T0 + timedelta(minutes=opened_min if opened_min is not None else first_min)
    return Found(iid, node, gpus, ftype, "sev2", opened, t, t + timedelta(minutes=10))


def test_recall_ttd_classification_and_precision() -> None:
    truths = [
        truth("a", "pcie_degradation", 60),
        truth("b", "ecc_degradation", 200, gpus=(3,)),
        truth("c", "thermal_runaway", 400, node="n2", scope="node", gpus=(0, 1, 2, 3)),
    ]
    incidents = [
        found("i1", "pcie_degradation", 61, opened_min=63),  # 3 min to detect, right type
        found("i2", "thermal_runaway", 401, node="n2", gpus=(5,)),  # node-level: any GPU matches
        found("i3", "unknown", 900),  # nothing near it: false alarm
        found("i4", "ecc_degradation", 205, gpus=(1,)),  # wrong GPU: doesn't match fault b
    ]
    r = score(truths, incidents)
    assert r["per_type"]["pcie_degradation"]["ttd_median_s"] == 180
    assert r["per_type"]["ecc_degradation"]["recall"] == 0
    assert r["overall"]["detected"] == 2 and r["overall"]["classified_correctly"] == 2
    assert r["overall"]["precision"] == 0.5  # i1, i2 true; i3, i4 false
    assert r["overall"]["false_alarms_by_type"] == {"ecc_degradation": 1, "unknown": 1}
    assert [m["fault_id"] for m in r["missed"]] == ["b"]
    assert "pcie_degradation" in to_markdown(r, "t")


def test_decoys_count_alarms_as_false_positives_and_labels_as_correct() -> None:
    truths = [
        truth("d1", "noisy_neighbor", 60, is_fault=False),
        truth("d2", "noisy_neighbor", 300, is_fault=False),
        truth("d3", "noisy_neighbor", 600, is_fault=False),
    ]
    incidents = [found("i1", "noisy_neighbor", 62), found("i2", "thermal_runaway", 301)]
    r = score(truths, incidents)
    assert r["decoys"] == {
        "decoys": 3,
        "raised_alarm": 1,
        "labelled_noisy_neighbor": 1,
        "ignored": 1,
    }
    assert r["overall"]["precision"] == 0.0  # the only alarm was on a decoy
