"""A test client for the agent API with in-memory people, traces and approvals."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from agent_testkit import INCIDENT_ID, toolbox
from fastapi.testclient import TestClient

from agent.app import Services, create_app
from agent.data import DecisionError
from agent.llm import ScriptedProvider
from agent.loop import Investigator
from agent.people import User, hash_password, verify_password
from agent.runtime import Settings
from agent.trace import MemoryTraceStore

UI = {"X-Requested-With": "ia-ui"}
BOT_KEY = "bot-key"


@dataclass
class FakePeople:
    users: dict[str, tuple[User, str]] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)
    feedback: list[tuple[uuid.UUID, str, int, str | None, str]] = field(default_factory=list)

    def add(self, username: str, role: str, password: str = "pw-123456", slack: str | None = None
            ) -> User:  # fmt: skip
        user = User(username, username.title(), role, slack)  # type: ignore[arg-type]
        self.users[username] = (user, hash_password(password))
        return user

    async def login(self, username: str, password: str) -> tuple[User, str] | None:
        entry = self.users.get(username)
        if entry is None or not verify_password(password, entry[1]):
            return None
        token = uuid.uuid4().hex
        self.sessions[token] = username
        return entry[0], token

    async def session_user(self, token: str) -> User | None:
        name = self.sessions.get(token)
        return self.users[name][0] if name else None

    async def logout(self, token: str) -> None:
        self.sessions.pop(token, None)

    async def by_slack_id(self, slack_user_id: str) -> User | None:
        return next((u for u, _ in self.users.values() if u.slack_user_id == slack_user_id), None)

    async def add_feedback(
        self, run_id: uuid.UUID, user: User, rating: int, comment: str | None, source: str
    ) -> None:
        self.feedback.append((run_id, user.username, rating, comment, source))

    async def feedback_report(self, days: int) -> dict[str, Any]:
        return {"days": days, "up": sum(f[2] == 1 for f in self.feedback),
                "down": sum(f[2] == -1 for f in self.feedback), "by_root_cause": [],
                "recent": []}  # fmt: skip

    async def audit(self, request_id: uuid.UUID | None, limit: int) -> list[dict[str, Any]]:
        return []

    async def incidents(self, detector_run: str, status: str | None, limit: int
                        ) -> list[dict[str, Any]]:  # fmt: skip
        return [{"incident_id": INCIDENT_ID, "title": "Thermal runaway", "status": "open"}]

    async def incident_detail(self, incident_id: uuid.UUID) -> dict[str, Any] | None:
        if incident_id != INCIDENT_ID:
            return None
        return {"incident": {"incident_id": str(INCIDENT_ID)}, "runs": [], "approvals": []}

    async def followups(self, incident_id: uuid.UUID, limit: int) -> list[dict[str, Any]]:
        return []

    async def detector_runs(self) -> list[dict[str, Any]]:
        return [{"detector_run": "live", "incidents": 1}]


@dataclass
class FakeReader:
    traces: MemoryTraceStore

    async def run(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        r = self.traces.runs.get(run_id)
        return {"run_id": run_id, "status": r.status} if r else None

    async def latest_diagnosis(self, incident_id: uuid.UUID) -> dict[str, Any] | None:
        done = [r for r in self.traces.runs.values()
                if r.incident_id == incident_id and r.status == "succeeded"
                and r.config.get("kind") != "followup"]  # fmt: skip
        return {"run_id": done[-1].run_id, "diagnosis": done[-1].diagnosis} if done else None

    async def next_uninvestigated(self, detector_run: str) -> uuid.UUID | None:
        return None


@dataclass
class FakeDecider:
    decided: list[tuple[uuid.UUID, bool, str, str | None]] = field(default_factory=list)

    async def decide(
        self, request_id: uuid.UUID, approve: bool, decided_by: str, note: str | None
    ) -> dict[str, Any]:
        if any(d[0] == request_id for d in self.decided):
            raise DecisionError(f"approval request {request_id} was already decided")
        self.decided.append((request_id, approve, decided_by, note))
        return {"request_id": str(request_id), "status": "approved" if approve else "rejected",
                "decided_by": decided_by}  # fmt: skip


def app_client(
    script: list[Any],
) -> tuple[TestClient, FakePeople, FakeDecider, MemoryTraceStore]:
    tb, data, _, approvals = toolbox()
    people, decider, traces = FakePeople(), FakeDecider(), MemoryTraceStore()
    provider = ScriptedProvider(script)
    services = Services(
        data, approvals, FakeReader(traces),
        lambda: Investigator(provider, tb, approvals, traces), decider, people,
    )  # fmt: skip
    settings = Settings(api_keys=frozenset({"agent-key"}), slack_bot_keys=frozenset({BOT_KEY}))
    return TestClient(create_app(settings, services)), people, decider, traces


def login(c: TestClient, username: str, password: str = "pw-123456") -> None:
    r = c.post("/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
