"""Slack bot: posts new incidents to a channel, threads the diagnosis under each one, and lets
people approve or reject actions, rate diagnoses and ask follow-up questions in the thread.

Everything a person does goes through the agent API's people routes (agent.web) with the bot's
key and the clicking user's Slack id, so roles, the approval audit log and feedback work exactly
as in the web UI. A Slack user who isn't linked to an account (`agent users add ... --slack U..`)
can read but not act.

Socket Mode: the bot opens an outbound WebSocket to Slack, so nothing here has to be reachable
from the internet. Run with `agent slack` (needs SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL).

The logic lives in SlackBot with a minimal Slack client interface, so it's tested with a fake
client; `run_socket_mode` is the thin Bolt adapter around it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg
import httpx2

from agent.people import PgPeople

log = logging.getLogger("agent.slack")

APPROVE, REJECT, THUMBS_UP, THUMBS_DOWN = "approve", "reject", "feedback_up", "feedback_down"
SEVERITY_EMOJI = {
    "sev1": ":red_circle:",
    "sev2": ":large_orange_circle:",
    "sev3": ":large_yellow_circle:",
}


class SlackClient(Protocol):
    async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: N802
    async def chat_update(self, **kwargs: Any) -> dict[str, Any]: ...
    async def chat_postEphemeral(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: N802


class Store(Protocol):
    async def unposted_incidents(self, detector_run: str, limit: int) -> list[dict[str, Any]]: ...
    async def posts_needing_diagnosis(self) -> list[dict[str, Any]]: ...
    async def mark_posted(self, incident_id: uuid.UUID, channel: str, ts: str) -> None: ...
    async def mark_diagnosed(self, incident_id: uuid.UUID, run_id: uuid.UUID) -> None: ...
    async def incident_for_thread(self, channel: str, thread_ts: str) -> uuid.UUID | None: ...
    async def detail(self, incident_id: uuid.UUID) -> dict[str, Any] | None: ...


def _label(s: str | None) -> str:
    return (s or "-").replace("_", " ")


def incident_blocks(inc: dict[str, Any], web_url: str | None) -> list[dict[str, Any]]:
    gpus = ", ".join(map(str, inc["gpu_indices"])) if inc["gpu_indices"] else "node-level"
    link = f" · <{web_url}/incidents/{inc['incident_id']}|open in the web UI>" if web_url else ""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"{SEVERITY_EMOJI.get(inc['severity'], ':white_circle:')} *{inc['title']}*\n"
            f"`{inc['node_id']}` · GPUs {gpus} · {inc['severity']} · detector suspects "
            f"_{_label(inc['suspected_failure_type'])}_{link}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text":
            "The agent is investigating; the diagnosis will appear in this thread."}]},
    ]  # fmt: skip


def diagnosis_blocks(
    run_id: str, diagnosis: dict[str, Any], approvals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    action = diagnosis["recommended_action"]
    cites = ", ".join(f"`{c['chunk_id']}`" for c in diagnosis["citations"]) or "none"
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"*Diagnosis: {_label(diagnosis['root_cause'])}* "
            f"(confidence {round(diagnosis['confidence'] * 100)}%)\n{diagnosis['summary']}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"*Recommended: {_label(action['action'])}*\n{action['rationale']}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Sources: {cites}"}]},
    ]  # fmt: skip
    for a in approvals:
        target = a["node_id"] + (f" GPU {a['gpu_index']}" if a.get("gpu_index") is not None else "")
        if a["status"] == "pending":
            blocks += [
                {"type": "section", "text": {"type": "mrkdwn", "text":
                    f":hourglass: *{_label(a['action'])}* on `{target}` needs approval."}},
                {"type": "actions", "block_id": f"approval:{a['request_id']}", "elements": [
                    {"type": "button", "style": "primary", "action_id": APPROVE,
                     "text": {"type": "plain_text", "text": "Approve"},
                     "value": str(a["request_id"])},
                    {"type": "button", "style": "danger", "action_id": REJECT,
                     "text": {"type": "plain_text", "text": "Reject"},
                     "value": str(a["request_id"])},
                ]},
            ]  # fmt: skip
        else:
            outcome = f"*{_label(a['action'])}* on `{target}`: {a['status']} by {a['decided_by']}"
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": outcome}]})
    blocks.append({"type": "actions", "block_id": f"feedback:{run_id}", "elements": [
        {"type": "button", "action_id": THUMBS_UP, "value": run_id,
         "text": {"type": "plain_text", "text": ":+1: Helpful"}},
        {"type": "button", "action_id": THUMBS_DOWN, "value": run_id,
         "text": {"type": "plain_text", "text": ":-1: Not helpful"}},
    ]})  # fmt: skip
    return blocks


def answer_text(answer: dict[str, Any] | None, status: str, error: str | None) -> str:
    if answer is None:
        return f":warning: I couldn't answer that ({status}{': ' + error if error else ''})."
    cites = ", ".join(f"`{c['chunk_id']}`" for c in answer["citations"])
    return str(answer["answer"]) + (f"\n_Sources: {cites}_" if cites else "")


@dataclass
class SlackBot:
    slack: SlackClient
    api: httpx2.AsyncClient  # agent API, with X-API-Key = SLACK_BOT_API_KEY
    store: Store
    channel: str
    detector_run: str = "live"
    web_url: str | None = None

    async def poll_once(self) -> None:
        """Post new incidents, then thread diagnoses that have appeared since."""
        for inc in await self.store.unposted_incidents(self.detector_run, 20):
            r = await self.slack.chat_postMessage(
                channel=self.channel, text=inc["title"],
                blocks=incident_blocks(inc, self.web_url),
            )  # fmt: skip
            await self.store.mark_posted(inc["incident_id"], self.channel, r["ts"])
        for post in await self.store.posts_needing_diagnosis():
            detail = await self.store.detail(post["incident_id"])
            if not detail or not detail.get("diagnosis"):
                continue
            d = detail["diagnosis"]
            await self.slack.chat_postMessage(
                channel=post["channel"], thread_ts=post["ts"],
                text=f"Diagnosis: {_label(d['diagnosis']['root_cause'])}",
                blocks=diagnosis_blocks(str(d["run_id"]), d["diagnosis"], detail["approvals"]),
            )  # fmt: skip
            await self.store.mark_diagnosed(post["incident_id"], uuid.UUID(str(d["run_id"])))

    def _as(self, slack_user: str) -> dict[str, str]:
        return {"X-Slack-User": slack_user}

    async def on_decision(self, body: dict[str, Any]) -> None:
        action = body["actions"][0]
        user, channel = body["user"]["id"], body["channel"]["id"]
        decision = "approve" if action["action_id"] == APPROVE else "reject"
        r = await self.api.post(
            f"/v1/ui/approvals/{action['value']}/decision",
            json={"decision": decision, "note": "via Slack"}, headers=self._as(user),
        )  # fmt: skip
        if r.status_code != 200:
            await self.slack.chat_postEphemeral(channel=channel, user=user,
                                                text=f":no_entry: {_api_error(r)}")  # fmt: skip
            return
        row = r.json()
        # Replace the buttons with the outcome, so nobody clicks a decided request again.
        message = body["message"]
        blocks = [
            b for b in message["blocks"] if b.get("block_id") != f"approval:{action['value']}"
        ]
        blocks.insert(len(blocks) - 1, {"type": "context", "elements": [{"type": "mrkdwn", "text":
            f":white_check_mark: *{decision}d* by {row['decided_by']} (<@{user}>)"}]})  # fmt: skip
        await self.slack.chat_update(channel=channel, ts=message["ts"], blocks=blocks,
                                     text=message.get("text", ""))  # fmt: skip

    async def on_feedback(self, body: dict[str, Any]) -> None:
        action = body["actions"][0]
        user, channel = body["user"]["id"], body["channel"]["id"]
        rating = 1 if action["action_id"] == THUMBS_UP else -1
        r = await self.api.post(f"/v1/ui/runs/{action['value']}/feedback",
                                json={"rating": rating}, headers=self._as(user))  # fmt: skip
        text = "Thanks, feedback recorded." if r.status_code == 200 else _api_error(r)
        await self.slack.chat_postEphemeral(channel=channel, user=user, text=text)

    async def on_thread_message(self, event: dict[str, Any]) -> None:
        """A question in an incident's thread (the bot mentioned, or any reply)."""
        thread_ts, channel = event.get("thread_ts"), event["channel"]
        if not thread_ts or event.get("bot_id") or event.get("subtype"):
            return
        incident_id = await self.store.incident_for_thread(channel, thread_ts)
        if incident_id is None:
            return
        question = _strip_mentions(event.get("text", ""))
        if len(question) < 3:
            return
        await self.slack.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=":mag: Looking into it (1-3 minutes)..."
        )
        r = await self.api.post(f"/v1/ui/incidents/{incident_id}/ask",
                                json={"question": question}, headers=self._as(event["user"]),
                                timeout=900)  # fmt: skip
        if r.status_code != 200:
            text = f":no_entry: {_api_error(r)}"
        else:
            body = r.json()
            text = answer_text(body["answer"], body["status"], body["error"])
        await self.slack.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)


def _strip_mentions(text: str) -> str:
    import re

    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


def _api_error(r: httpx2.Response) -> str:
    try:
        detail = str(r.json().get("detail") or "") or None
    except (json.JSONDecodeError, AttributeError):
        detail = None
    if r.status_code == 403 and detail == "Slack user is not linked":
        return "Your Slack account isn't linked to an Incident Assistant account."
    return f"{detail or 'request failed'} ({r.status_code})"


class PgStore:
    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self.pool = pool
        self.people = PgPeople(pool)

    async def unposted_incidents(self, detector_run: str, limit: int) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT incident_id, title, severity, suspected_failure_type, node_id, gpu_indices "
            "FROM incidents i WHERE detector_run = $1 AND severity <> 'sev4' "
            "AND NOT EXISTS (SELECT 1 FROM slack_posts p WHERE p.incident_id = i.incident_id) "
            "ORDER BY opened_at LIMIT $2",
            detector_run, limit,
        )  # fmt: skip
        return [dict(r) for r in rows]

    async def posts_needing_diagnosis(self) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT p.incident_id, p.channel, p.ts FROM slack_posts p WHERE EXISTS ("
            " SELECT 1 FROM agent_runs r WHERE r.incident_id = p.incident_id "
            " AND r.status = 'succeeded' AND r.config->>'kind' IS DISTINCT FROM 'followup' "
            " AND r.run_id IS DISTINCT FROM p.run_id AND r.started_at > p.posted_at "
            "    - interval '1 day')"
        )
        return [dict(r) for r in rows]

    async def mark_posted(self, incident_id: uuid.UUID, channel: str, ts: str) -> None:
        await self.pool.execute(
            "INSERT INTO slack_posts (incident_id, channel, ts) VALUES ($1, $2, $3) "
            "ON CONFLICT (incident_id) DO NOTHING",
            incident_id, channel, ts,
        )  # fmt: skip

    async def mark_diagnosed(self, incident_id: uuid.UUID, run_id: uuid.UUID) -> None:
        await self.pool.execute(
            "UPDATE slack_posts SET run_id = $2 WHERE incident_id = $1", incident_id, run_id
        )

    async def incident_for_thread(self, channel: str, thread_ts: str) -> uuid.UUID | None:
        incident_id: uuid.UUID | None = await self.pool.fetchval(
            "SELECT incident_id FROM slack_posts WHERE channel = $1 AND ts = $2", channel, thread_ts
        )
        return incident_id

    async def detail(self, incident_id: uuid.UUID) -> dict[str, Any] | None:
        from agent.trace import PgTraceReader

        detail = await self.people.incident_detail(incident_id)
        if detail is not None:
            detail["diagnosis"] = await PgTraceReader(self.pool).latest_diagnosis(incident_id)
        return detail


async def run_socket_mode() -> None:  # pragma: no cover - needs a Slack workspace
    import os

    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_bolt.async_app import AsyncApp

    from agent.runtime import Settings

    settings = Settings.from_env()
    app = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    api = httpx2.AsyncClient(
        base_url=os.environ.get("AGENT_API_URL", "http://localhost:8002"),
        headers={"X-API-Key": os.environ["SLACK_BOT_API_KEY"]}, timeout=30,
    )  # fmt: skip
    bot = SlackBot(app.client, api, PgStore(pool), os.environ["SLACK_CHANNEL"],  # type: ignore[arg-type]
                   web_url=os.environ.get("WEB_URL"))  # fmt: skip

    # Keep references: a task with no reference can be garbage-collected before it finishes.
    background: set[asyncio.Task[None]] = set()

    def spawn(coro: Any) -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    async def ack_then(handler: Any, ack: Any, body: dict[str, Any]) -> None:
        await ack()  # Slack wants an ack within 3 s; the work happens after
        spawn(handler(body))

    @app.action(APPROVE)
    @app.action(REJECT)
    async def _decide(ack: Any, body: dict[str, Any]) -> None:
        await ack_then(bot.on_decision, ack, body)

    @app.action(THUMBS_UP)
    @app.action(THUMBS_DOWN)
    async def _feedback(ack: Any, body: dict[str, Any]) -> None:
        await ack_then(bot.on_feedback, ack, body)

    @app.event("app_mention")
    @app.event("message")
    async def _message(event: dict[str, Any]) -> None:
        spawn(bot.on_thread_message(event))

    async def poller() -> None:
        while True:
            try:
                await bot.poll_once()
            except Exception:
                log.exception("slack poll failed")
            await asyncio.sleep(30)

    spawn(poller())
    handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    await handler.start_async()  # type: ignore[no-untyped-call]
