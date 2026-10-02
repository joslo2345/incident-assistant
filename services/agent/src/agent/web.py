"""API for people: the web UI and the Slack bot (A7).

Every route here needs a person:
- The browser sends a session cookie (HttpOnly, SameSite=Strict) from POST /v1/auth/login.
  Requests that change something must also carry `X-Requested-With: ia-ui`, which a cross-site
  form can't set, so a malicious page can't ride on the cookie.
- The Slack bot sends its own key (SLACK_BOT_API_KEYS) plus `X-Slack-User`, and acts as the
  account mapped to that Slack user, with that account's role. Unmapped Slack users get nothing.

Approval decisions are recorded under the logged-in username; nobody types a name any more.
"""

# No `from __future__ import annotations` here: FastAPI resolves the Annotated dependency aliases
# defined inside build_router at runtime, and string annotations can't see closure locals.
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from agent.data import DecisionError
from agent.people import User

if TYPE_CHECKING:
    from agent.app import Services

SESSION_COOKIE = "ia_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "ia-ui"
MAX_FAILED_LOGINS = 5
LOCKOUT_S = 300


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class FeedbackRequest(BaseModel):
    rating: Literal[1, -1]
    comment: str | None = Field(default=None, max_length=2000)


class DecisionBody(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


def _user_json(u: User) -> dict[str, Any]:
    return {"username": u.username, "display_name": u.display_name, "role": u.role,
            "can_approve": u.can_approve}  # fmt: skip


def build_router(
    services: Callable[[], "Services"],
    run_exclusive: Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]],
) -> APIRouter:
    """`run_exclusive` serializes model work (investigations, follow-ups) with the watcher."""
    router = APIRouter()
    failed_logins: dict[str, list[float]] = {}

    async def current_user(request: Request) -> User:
        svc = services()
        if token := request.cookies.get(SESSION_COOKIE):
            user = await svc.people.session_user(token)
            if user is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
            if request.method not in {"GET", "HEAD"} and (
                request.headers.get(CSRF_HEADER) != CSRF_VALUE
            ):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")
            request.state.via = "web"
            return user
        key = request.headers.get("X-API-Key")
        slack_user = request.headers.get("X-Slack-User")
        bot_keys = request.app.state.settings.slack_bot_keys
        if key and slack_user and any(secrets.compare_digest(key, k) for k in bot_keys):
            user = await svc.people.by_slack_id(slack_user)
            if user is None:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Slack user is not linked")
            request.state.via = "slack"
            return user
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Log in first")

    Person = Annotated[User, Depends(current_user)]  # noqa: N806 (FastAPI alias)

    def require_approver(user: Person) -> User:
        if not user.can_approve:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Approver role required")
        return user

    Approver = Annotated[User, Depends(require_approver)]  # noqa: N806 (FastAPI alias)

    # auth -------------------------------------------------------------------------------------
    @router.post("/v1/auth/login", summary="Log in; sets a session cookie")
    async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
        now = time.monotonic()
        recent = [t for t in failed_logins.get(body.username, []) if now - t < LOCKOUT_S]
        if len(recent) >= MAX_FAILED_LOGINS:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed logins")
        result = await services().people.login(body.username, body.password)
        if result is None:
            failed_logins[body.username] = [*recent, now]
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
        failed_logins.pop(body.username, None)
        user, token = result
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, samesite="strict",
            secure=request.app.state.settings.secure_cookies, max_age=12 * 3600, path="/",
        )  # fmt: skip
        return _user_json(user)

    @router.post("/v1/auth/logout", summary="End the session")
    async def logout(request: Request, response: Response, _: Person) -> dict[str, str]:
        if token := request.cookies.get(SESSION_COOKIE):
            await services().people.logout(token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"status": "logged out"}

    @router.get("/v1/auth/me", summary="Who is logged in")
    async def me(user: Person) -> dict[str, Any]:
        return _user_json(user)

    # incidents --------------------------------------------------------------------------------
    @router.get("/v1/ui/incidents", summary="Incidents with their latest diagnosis")
    async def incidents(
        _: Person,
        run: Annotated[str, Query(max_length=64)] = "live",
        status_: Annotated[str | None, Query(alias="status", max_length=16)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        return await services().people.incidents(run, status_, limit)

    @router.get("/v1/ui/detector-runs", summary="Detector runs to browse")
    async def detector_runs(_: Person) -> list[dict[str, Any]]:
        return await services().people.detector_runs()

    @router.get("/v1/ui/incidents/{incident_id}", summary="Incident, diagnosis, approvals, chat")
    async def incident(incident_id: uuid.UUID, _: Person) -> dict[str, Any]:
        svc = services()
        detail = await svc.people.incident_detail(incident_id)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such incident")
        detail["diagnosis"] = await svc.reader.latest_diagnosis(incident_id)
        detail["followups"] = await svc.people.followups(incident_id, 50)
        return detail

    @router.get("/v1/ui/incidents/{incident_id}/metrics", summary="GPU series for charts")
    async def incident_metrics(
        incident_id: uuid.UUID,
        _: Person,
        gpu: Annotated[int, Query(ge=0, lt=16)],
        before_min: Annotated[int, Query(ge=5, le=360)] = 60,
        after_min: Annotated[int, Query(ge=0, le=360)] = 30,
    ) -> dict[str, Any]:
        from datetime import timedelta

        svc = services()
        inc = await svc.data.incident(incident_id)
        if inc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such incident")
        start = min(e.window_start for e in inc.evidence) - timedelta(minutes=before_min)
        end = max(e.window_end for e in inc.evidence) + timedelta(minutes=after_min)
        node = inc.components[0].node_id
        rows = await svc.data.metrics(node, gpu, start, end, timedelta(minutes=1))
        return {"node_id": node, "gpu_index": gpu, "start": start, "end": end, "series": rows}

    @router.get("/v1/ui/runs/{run_id}", summary="A run with its tool-call trace")
    async def run_trace(run_id: uuid.UUID, _: Person) -> dict[str, Any]:
        run = await services().reader.run(run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
        return run

    @router.get("/v1/ui/chunks/{chunk_id:path}", summary="A cited knowledge-base passage")
    async def chunk(chunk_id: str, _: Person) -> dict[str, Any]:
        client = services().knowledge_client
        if client is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Knowledge API unavailable")
        r = await client.get(f"/v1/chunks/{quote(chunk_id, safe='')}")
        if r.status_code == 404:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such passage")
        r.raise_for_status()
        result: dict[str, Any] = r.json()
        return result

    @router.post("/v1/ui/incidents/{incident_id}/ask", summary="Ask a follow-up question")
    async def ask(incident_id: uuid.UUID, body: AskRequest, user: Person) -> dict[str, Any]:
        svc = services()
        inc = await svc.data.incident(incident_id)
        if inc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such incident")
        latest = await svc.reader.latest_diagnosis(incident_id)
        history: list[tuple[str, str]] = []
        for f in await svc.people.followups(incident_id, 6):
            if f["answer"]:
                history += [("Operator", f["question"]), ("Assistant", f["answer"]["answer"])]

        async def work() -> Any:
            return await svc.investigator().ask(
                inc, body.question, latest["diagnosis"] if latest else None, history,
                asked_by=user.username,
            )  # fmt: skip

        result = await run_exclusive(work)
        return {
            "run_id": result.run.run_id, "status": result.run.status,
            "answer": result.answer.model_dump(mode="json") if result.answer else None,
            "error": result.run.error, "latency_ms": result.run.latency_ms,
        }  # fmt: skip

    @router.post("/v1/ui/incidents/{incident_id}/investigate", summary="Run a new investigation")
    async def reinvestigate(incident_id: uuid.UUID, _: Approver) -> dict[str, Any]:
        svc = services()
        inc = await svc.data.incident(incident_id)
        if inc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such incident")
        result = await run_exclusive(lambda: svc.investigator().investigate(inc))
        return {"run_id": result.run.run_id, "status": result.run.status}

    # approvals, feedback, audit ------------------------------------------------------------
    @router.post("/v1/ui/approvals/{request_id}/decision", summary="Approve or reject")
    async def decide(request_id: uuid.UUID, body: DecisionBody, user: Approver) -> dict[str, Any]:
        decider = services().decider
        if decider is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Approvals not configured")
        try:
            return await decider.decide(
                request_id, body.decision == "approve", user.username, body.note
            )
        except DecisionError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @router.post("/v1/ui/runs/{run_id}/feedback", summary="Thumbs up or down on a run")
    async def feedback(
        run_id: uuid.UUID, body: FeedbackRequest, request: Request, user: Person
    ) -> dict[str, str]:
        svc = services()
        if await svc.reader.run(run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
        source = getattr(request.state, "via", "web")
        await svc.people.add_feedback(run_id, user, body.rating, body.comment, source)
        return {"status": "recorded"}

    @router.get("/v1/ui/feedback", summary="Feedback report")
    async def feedback_report(
        _: Person, days: Annotated[int, Query(ge=1, le=365)] = 30
    ) -> dict[str, Any]:
        return await services().people.feedback_report(days)

    @router.get("/v1/ui/audit", summary="Approval audit log")
    async def audit(
        _: Person,
        request_id: uuid.UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        return await services().people.audit(request_id, limit)

    return router
