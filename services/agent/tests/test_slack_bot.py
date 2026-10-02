"""The Slack bot against a fake Slack client and the real agent API (in-process, ASGI)."""

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx2
from agent_testkit import INCIDENT_ID, PAST, RUNBOOK, call, make_incident, turn, valid_submission
from web_testkit import BOT_KEY, app_client

from agent.diagnosis import ANSWER, SUBMIT
from agent.slack_bot import (
    APPROVE,
    REJECT,
    THUMBS_DOWN,
    SlackBot,
    diagnosis_blocks,
    incident_blocks,
)


@dataclass
class FakeSlack:
    posted: list[dict[str, Any]] = field(default_factory=list)
    updated: list[dict[str, Any]] = field(default_factory=list)
    ephemeral: list[dict[str, Any]] = field(default_factory=list)

    async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        self.posted.append(kwargs)
        return {"ok": True, "ts": f"1700000000.{len(self.posted):06d}"}

    async def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        self.updated.append(kwargs)
        return {"ok": True}

    async def chat_postEphemeral(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        self.ephemeral.append(kwargs)
        return {"ok": True}


ROW = {"incident_id": INCIDENT_ID, "title": "Thermal runaway on h100-node-02 GPU 3",
       "severity": "sev2", "suspected_failure_type": "thermal_runaway", "node_id": "h100-node-02",
       "gpu_indices": [3]}  # fmt: skip


@dataclass
class FakeStore:
    detail_data: dict[str, Any] | None = None
    posts: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)

    async def unposted_incidents(self, detector_run: str, limit: int) -> list[dict[str, Any]]:
        return [] if INCIDENT_ID in self.posts else [ROW]

    async def posts_needing_diagnosis(self) -> list[dict[str, Any]]:
        return [{"incident_id": k, **v} for k, v in self.posts.items() if not v.get("run_id")]

    async def mark_posted(self, incident_id: uuid.UUID, channel: str, ts: str) -> None:
        self.posts[incident_id] = {"channel": channel, "ts": ts}

    async def mark_diagnosed(self, incident_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.posts[incident_id]["run_id"] = run_id

    async def incident_for_thread(self, channel: str, thread_ts: str) -> uuid.UUID | None:
        return next((k for k, v in self.posts.items() if v["ts"] == thread_ts), None)

    async def detail(self, incident_id: uuid.UUID) -> dict[str, Any] | None:
        return self.detail_data


def bot_for(script: list[Any]) -> tuple[SlackBot, FakeSlack, FakeStore, Any, Any]:
    c, people, decider, _ = app_client(script)
    api = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=c.app), base_url="http://agent",
                             headers={"X-API-Key": BOT_KEY})  # fmt: skip
    slack, store = FakeSlack(), FakeStore()
    people.add("alice", "approver", slack="U_ALICE")
    people.add("victor", "viewer", slack="U_VICTOR")
    return SlackBot(slack, api, store, "C_INCIDENTS", web_url="http://localhost:3001"), slack, \
        store, decider, people  # fmt: skip


DIAG = valid_submission() | {
    "recommended_action": {"action": "drain_node", "rationale": "fan"},
    "citations": [{"chunk_id": RUNBOOK.chunk_id}],
    "confidence": 0.85,
}
PENDING = {"request_id": str(uuid.uuid4()), "action": "drain_node", "node_id": "h100-node-02",
           "gpu_index": None, "status": "pending", "decided_by": None}  # fmt: skip


async def test_new_incident_is_posted_then_the_diagnosis_threaded_once() -> None:
    bot, slack, store, *_ = bot_for([])
    await bot.poll_once()
    assert len(slack.posted) == 1 and slack.posted[0]["channel"] == "C_INCIDENTS"
    assert "open in the web UI" in str(slack.posted[0]["blocks"])
    await bot.poll_once()  # no diagnosis yet: nothing more
    assert len(slack.posted) == 1

    store.detail_data = {"diagnosis": {"run_id": str(uuid.uuid4()), "diagnosis": DIAG},
                         "approvals": [PENDING]}  # fmt: skip
    await bot.poll_once()
    await bot.poll_once()
    assert len(slack.posted) == 2  # threaded exactly once
    thread = slack.posted[1]
    assert thread["thread_ts"] == store.posts[INCIDENT_ID]["ts"] == "1700000000.000001"
    actions = [e["action_id"] for b in thread["blocks"] if b["type"] == "actions"
               for e in b["elements"]]  # fmt: skip
    assert actions == [APPROVE, REJECT, "feedback_up", THUMBS_DOWN]


def decision_body(user: str, action_id: str) -> dict[str, Any]:
    blocks = diagnosis_blocks("run-1", DIAG, [PENDING])
    return {
        "user": {"id": user},
        "channel": {"id": "C_INCIDENTS"},
        "actions": [{"action_id": action_id, "value": PENDING["request_id"]}],
        "message": {"ts": "1700000000.000002", "blocks": blocks, "text": "Diagnosis"},
    }


async def test_approval_from_slack_is_recorded_as_the_linked_user() -> None:
    bot, slack, _, decider, _ = bot_for([])
    await bot.on_decision(decision_body("U_ALICE", APPROVE))
    assert decider.decided[0][1:3] == (True, "alice")
    updated = str(slack.updated[0]["blocks"])
    assert "approved* by alice" in updated
    assert f"approval:{PENDING['request_id']}" not in updated  # buttons removed


async def test_viewers_and_unlinked_slack_users_cannot_decide() -> None:
    bot, slack, _, decider, _ = bot_for([])
    await bot.on_decision(decision_body("U_VICTOR", APPROVE))
    await bot.on_decision(decision_body("U_STRANGER", REJECT))
    assert decider.decided == [] and slack.updated == []
    assert "Approver role required" in slack.ephemeral[0]["text"]
    assert "isn't linked" in slack.ephemeral[1]["text"]


async def test_follow_up_question_in_the_thread_is_answered_there() -> None:
    sim = call("find_similar_incidents", {"incident_id": str(INCIDENT_ID)})
    answer = call(ANSWER, {"answer": "Yes: INC-0042, same fan.", "citations": [PAST.chunk_id]})
    bot, slack, store, *_ = bot_for([turn(sim), turn(answer)])
    store.posts[INCIDENT_ID] = {"channel": "C_INCIDENTS", "ts": "1700.1"}
    question = "<@UBOT> seen this before?"
    await bot.on_thread_message(
        {"channel": "C_INCIDENTS", "thread_ts": "1700.1", "user": "U_VICTOR", "text": question}
    )
    assert [p["thread_ts"] for p in slack.posted] == ["1700.1", "1700.1"]
    assert slack.posted[1]["text"].startswith("Yes: INC-0042")
    assert PAST.chunk_id in slack.posted[1]["text"]


async def test_bot_ignores_its_own_messages_and_unrelated_threads() -> None:
    bot, slack, store, *_ = bot_for([])
    store.posts[INCIDENT_ID] = {"channel": "C_INCIDENTS", "ts": "1700.1"}
    await bot.on_thread_message({"channel": "C_INCIDENTS", "thread_ts": "1700.1", "user": "U_X",
                                 "bot_id": "B1", "text": "Looking into it"})  # fmt: skip
    await bot.on_thread_message({"channel": "C_INCIDENTS", "thread_ts": "9999.9", "user": "U_X",
                                 "text": "unrelated thread"})  # fmt: skip
    await bot.on_thread_message({"channel": "C_INCIDENTS", "user": "U_X", "text": "top level"})
    assert slack.posted == []


async def test_feedback_from_slack() -> None:
    search = call("search_runbooks", {"query": "fan"})
    bot, slack, _, _, people = bot_for([turn(search), turn(call(SUBMIT, valid_submission()))])
    run = await bot.api.post(f"/v1/incidents/{INCIDENT_ID}/investigate",
                             headers={"X-API-Key": "agent-key"})  # fmt: skip
    run_id = run.json()["run_id"]
    await bot.on_feedback({"user": {"id": "U_VICTOR"}, "channel": {"id": "C"},
                           "actions": [{"action_id": THUMBS_DOWN, "value": run_id}]})  # fmt: skip
    assert people.feedback[0][1:] == ("victor", -1, None, "slack")
    assert slack.ephemeral[0]["text"].startswith("Thanks")


def test_blocks_render_without_a_web_url() -> None:
    assert "open in the web UI" not in str(incident_blocks(ROW, None))
    decided = PENDING | {"status": "approved", "decided_by": "alice"}
    text = str(diagnosis_blocks("r", DIAG, [decided]))
    assert "approved by alice" in text and f"'action_id': '{APPROVE}'" not in text
    assert make_incident().incident_id == INCIDENT_ID
