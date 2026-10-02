"""People and what they do: accounts, sessions, feedback, the approval audit log, and the views
the web UI and Slack bot read (incident list and detail).

Passwords are hashed with scrypt (stdlib). Session tokens are random; only their SHA-256 is
stored, so reading the database doesn't let anyone take over a session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import asyncpg

Role = Literal["viewer", "approver", "admin"]
ROLES: tuple[Role, ...] = ("viewer", "approver", "admin")
SESSION_TTL = timedelta(hours=12)

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, key = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(candidate.hex(), key)


# Checked against even when the username doesn't exist, so login timing doesn't reveal which
# usernames are real.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: Role
    slack_user_id: str | None = None

    @property
    def can_approve(self) -> bool:
        return self.role in {"approver", "admin"}


class People(Protocol):
    async def login(self, username: str, password: str) -> tuple[User, str] | None: ...
    async def session_user(self, token: str) -> User | None: ...
    async def logout(self, token: str) -> None: ...
    async def by_slack_id(self, slack_user_id: str) -> User | None: ...
    async def add_feedback(
        self, run_id: uuid.UUID, user: User, rating: int, comment: str | None, source: str
    ) -> None: ...
    async def feedback_report(self, days: int) -> dict[str, Any]: ...
    async def audit(self, request_id: uuid.UUID | None, limit: int) -> list[dict[str, Any]]: ...
    async def incidents(
        self, detector_run: str, status: str | None, limit: int
    ) -> list[dict[str, Any]]: ...
    async def incident_detail(self, incident_id: uuid.UUID) -> dict[str, Any] | None: ...
    async def followups(self, incident_id: uuid.UUID, limit: int) -> list[dict[str, Any]]: ...
    async def detector_runs(self) -> list[dict[str, Any]]: ...


def _user(r: asyncpg.Record) -> User:
    return User(r["username"], r["display_name"], r["role"], r["slack_user_id"])


class PgPeople:
    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self.pool = pool

    # accounts -----------------------------------------------------------------------------
    async def add_user(
        self, username: str, display_name: str, role: Role, password: str,
        slack_user_id: str | None = None,
    ) -> User:  # fmt: skip
        r = await self.pool.fetchrow(
            "INSERT INTO users (username, display_name, role, password_hash, slack_user_id) "
            "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (username) DO UPDATE SET "
            "display_name = EXCLUDED.display_name, role = EXCLUDED.role, "
            "password_hash = EXCLUDED.password_hash, slack_user_id = EXCLUDED.slack_user_id, "
            "disabled = false RETURNING username, display_name, role, slack_user_id",
            username, display_name, role, hash_password(password), slack_user_id,
        )  # fmt: skip
        assert r is not None
        return _user(r)

    async def login(self, username: str, password: str) -> tuple[User, str] | None:
        r = await self.pool.fetchrow(
            "SELECT username, display_name, role, slack_user_id, password_hash FROM users "
            "WHERE username = $1 AND NOT disabled",
            username,
        )
        if not verify_password(password, r["password_hash"] if r else _DUMMY_HASH) or r is None:
            return None
        token = secrets.token_urlsafe(32)
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM sessions WHERE expires_at < now()")
            await conn.execute(
                "INSERT INTO sessions (token_hash, username, expires_at) VALUES ($1, $2, $3)",
                _token_hash(token), username, datetime.now(UTC) + SESSION_TTL,
            )  # fmt: skip
        return _user(r), token

    async def session_user(self, token: str) -> User | None:
        r = await self.pool.fetchrow(
            "SELECT u.username, u.display_name, u.role, u.slack_user_id FROM sessions s "
            "JOIN users u USING (username) WHERE s.token_hash = $1 AND s.expires_at > now() "
            "AND NOT u.disabled",
            _token_hash(token),
        )
        return _user(r) if r else None

    async def logout(self, token: str) -> None:
        await self.pool.execute("DELETE FROM sessions WHERE token_hash = $1", _token_hash(token))

    async def by_slack_id(self, slack_user_id: str) -> User | None:
        r = await self.pool.fetchrow(
            "SELECT username, display_name, role, slack_user_id FROM users "
            "WHERE slack_user_id = $1 AND NOT disabled",
            slack_user_id,
        )
        return _user(r) if r else None

    # feedback and audit -------------------------------------------------------------------
    async def add_feedback(
        self, run_id: uuid.UUID, user: User, rating: int, comment: str | None, source: str
    ) -> None:
        await self.pool.execute(
            "INSERT INTO feedback (run_id, username, rating, comment, source) "
            "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (run_id, username) DO UPDATE SET "
            "rating = EXCLUDED.rating, comment = EXCLUDED.comment, source = EXCLUDED.source, "
            "created_at = now()",
            run_id, user.username, rating, comment, source,
        )  # fmt: skip

    async def feedback_report(self, days: int) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days)
        totals = await self.pool.fetchrow(
            "SELECT count(*) FILTER (WHERE rating = 1) AS up, "
            "count(*) FILTER (WHERE rating = -1) AS down FROM feedback WHERE created_at >= $1",
            since,
        )
        by_cause = await self.pool.fetch(
            "SELECT r.diagnosis->>'root_cause' AS root_cause, "
            "count(*) FILTER (WHERE f.rating = 1) AS up, "
            "count(*) FILTER (WHERE f.rating = -1) AS down "
            "FROM feedback f JOIN agent_runs r USING (run_id) WHERE f.created_at >= $1 "
            "GROUP BY 1 ORDER BY 3 DESC, 2 DESC",
            since,
        )
        recent = await self.pool.fetch(
            "SELECT f.created_at, f.username, f.rating, f.comment, f.source, f.run_id, "
            "r.incident_id, r.diagnosis->>'root_cause' AS root_cause, "
            "r.config->>'kind' AS kind FROM feedback f JOIN agent_runs r USING (run_id) "
            "WHERE f.created_at >= $1 ORDER BY f.created_at DESC LIMIT 50",
            since,
        )
        assert totals is not None
        return {
            "days": days,
            "up": totals["up"],
            "down": totals["down"],
            "by_root_cause": [dict(r) for r in by_cause],
            "recent": [dict(r) for r in recent],
        }

    async def audit(self, request_id: uuid.UUID | None, limit: int) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT id, at, request_id, event, actor, action, node_id, gpu_index, note "
            "FROM approval_audit WHERE $1::uuid IS NULL OR request_id = $1 "
            "ORDER BY at DESC, id DESC LIMIT $2",
            request_id, limit,
        )  # fmt: skip
        return [dict(r) for r in rows]

    # incident views -----------------------------------------------------------------------
    async def detector_runs(self) -> list[dict[str, Any]]:
        """Detector runs worth browsing: live first, then the final layer of evaluation runs."""
        rows = await self.pool.fetch(
            "SELECT detector_run, count(*) AS incidents, max(opened_at) AS latest FROM incidents "
            "WHERE detector_run = 'live' OR detector_run LIKE '%-v3-residual' "
            "GROUP BY 1 ORDER BY (detector_run = 'live') DESC, max(opened_at) DESC LIMIT 50"
        )
        return [dict(r) for r in rows]

    async def incidents(
        self, detector_run: str, status: str | None, limit: int
    ) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT i.incident_id, i.title, i.status, i.severity, i.suspected_failure_type,
                   i.node_id, i.gpu_indices, i.opened_at, i.last_signal_at,
                   d.run_id, d.diagnosis->>'root_cause' AS root_cause,
                   (d.diagnosis->>'confidence')::float8 AS confidence,
                   d.diagnosis->'recommended_action'->>'action' AS action,
                   (SELECT count(*) FROM approval_requests a
                     WHERE a.incident_id = i.incident_id AND a.status = 'pending') AS pending
            FROM incidents i
            LEFT JOIN LATERAL (
                SELECT run_id, diagnosis FROM agent_runs r
                WHERE r.incident_id = i.incident_id AND r.status = 'succeeded'
                  AND r.config->>'kind' IS DISTINCT FROM 'followup'
                ORDER BY r.started_at DESC LIMIT 1
            ) d ON true
            WHERE i.detector_run = $1 AND ($2::text IS NULL OR i.status = $2)
            ORDER BY i.opened_at DESC LIMIT $3
            """,
            detector_run, status, limit,
        )  # fmt: skip
        return [dict(r) for r in rows]

    async def incident_detail(self, incident_id: uuid.UUID) -> dict[str, Any] | None:
        inc = await self.pool.fetchrow(
            "SELECT record::text AS record, detector_run, opened_at FROM incidents "
            "WHERE incident_id = $1",
            incident_id,
        )
        if inc is None:
            return None
        runs = await self.pool.fetch(
            "SELECT run_id, started_at, status, model, steps, tool_calls, input_tokens, "
            "output_tokens, latency_ms, error FROM agent_runs WHERE incident_id = $1 "
            "AND config->>'kind' IS DISTINCT FROM 'followup' ORDER BY started_at DESC LIMIT 10",
            incident_id,
        )
        approvals = await self.pool.fetch(
            "SELECT request_id, created_at, action, node_id, gpu_index, reason, run_id, "
            "requested_by, status, decided_by, decided_at, decision_note "
            "FROM approval_requests WHERE incident_id = $1 ORDER BY created_at DESC",
            incident_id,
        )
        return {
            "incident": json.loads(inc["record"]),
            "detector_run": inc["detector_run"],
            "runs": [dict(r) for r in runs],
            "approvals": [dict(r) for r in approvals],
        }

    async def followups(self, incident_id: uuid.UUID, limit: int) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT run_id, started_at, status, config->>'question' AS question, "
            "config->>'asked_by' AS asked_by, diagnosis::text AS answer, latency_ms "
            "FROM agent_runs WHERE incident_id = $1 AND config->>'kind' = 'followup' "
            "ORDER BY started_at ASC LIMIT $2",
            incident_id, limit,
        )  # fmt: skip
        return [
            {**dict(r), "answer": json.loads(r["answer"]) if r["answer"] else None} for r in rows
        ]
