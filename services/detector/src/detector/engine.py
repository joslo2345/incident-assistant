"""Runs the rules over minute batches in time order and feeds the incident tracker."""

from __future__ import annotations

from detector.features import MinuteBatch
from detector.incidents import IncidentTracker, OpenIncident
from detector.rules import Alert, Config, RuleEngine


class Detector:
    def __init__(self, cfg: Config, run: str) -> None:
        self.rules = RuleEngine(cfg)
        self.tracker = IncidentTracker(run)
        self.alerts_total = 0

    def process(self, batch: MinuteBatch) -> list[OpenIncident]:
        alerts: list[Alert] = []
        for x in batch.xids:
            alerts += self.rules.xid_alerts(x)
        for b in batch.bmc:
            alerts += self.rules.bmc_alerts(b)
        for m in batch.gpus:
            alerts += self.rules.gpu_alerts(m)
        reporting = {m.node_id for m in batch.gpus}
        seen = {(m.node_id, m.gpu_index) for m in batch.gpus}
        alerts += self.rules.missing_alerts(batch.minute, reporting, seen)
        self.alerts_total += len(alerts)
        return self.tracker.update(batch.minute, alerts)
