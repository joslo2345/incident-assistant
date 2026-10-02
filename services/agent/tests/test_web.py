import uuid

from agent_testkit import INCIDENT_ID, PAST, call, turn, valid_submission
from web_testkit import BOT_KEY, UI, app_client, login

from agent.diagnosis import ANSWER, SUBMIT
from agent.people import hash_password, verify_password


def test_password_hashing() -> None:
    h = hash_password("correct horse")
    assert h.startswith("scrypt$") and "correct horse" not in h
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "md5$abc$def")  # unknown scheme fails closed
    assert hash_password("same") != hash_password("same")  # salted


def test_login_session_and_logout() -> None:
    c, people, *_ = app_client([])
    people.add("alice", "approver")
    with c:
        assert c.get("/v1/auth/me").status_code == 401
        assert c.post("/v1/auth/login", json={"username": "alice", "password": "nope"}
                      ).status_code == 401  # fmt: skip
        login(c, "alice")
        cookie = c.cookies.get("ia_session")
        assert cookie
        me = c.get("/v1/auth/me").json()
        assert me == {"username": "alice", "display_name": "Alice", "role": "approver",
                      "can_approve": True}  # fmt: skip
        assert c.post("/v1/auth/logout", headers=UI).status_code == 200
        c.cookies.set("ia_session", cookie)  # replaying the old cookie doesn't work
        assert c.get("/v1/auth/me").status_code == 401


def test_session_cookie_is_httponly_and_samesite_strict() -> None:
    c, people, *_ = app_client([])
    people.add("alice", "viewer")
    with c:
        r = c.post("/v1/auth/login", json={"username": "alice", "password": "pw-123456"})
        header = r.headers["set-cookie"].lower()
        assert "httponly" in header and "samesite=strict" in header


def test_repeated_failed_logins_are_locked_out() -> None:
    c, people, *_ = app_client([])
    people.add("alice", "viewer")
    with c:
        for _ in range(5):
            c.post("/v1/auth/login", json={"username": "alice", "password": "guess"})
        r = c.post("/v1/auth/login", json={"username": "alice", "password": "pw-123456"})
        assert r.status_code == 429  # even the right password, until the lockout ends


def test_state_changes_need_the_csrf_header() -> None:
    c, people, decider, _ = app_client([])
    people.add("alice", "approver")
    rid = uuid.uuid4()
    with c:
        login(c, "alice")
        body = {"decision": "approve"}
        # A cross-site form can post with the cookie but can't add the custom header.
        assert c.post(f"/v1/ui/approvals/{rid}/decision", json=body).status_code == 403
        assert c.post(f"/v1/ui/approvals/{rid}/decision", json=body, headers=UI).status_code == 200
    assert decider.decided[0][2] == "alice"


def test_decisions_are_recorded_under_the_logged_in_user_and_need_the_role() -> None:
    c, people, decider, _ = app_client([])
    people.add("victor", "viewer")
    people.add("alice", "approver")
    rid = uuid.uuid4()
    with c:
        login(c, "victor")
        r = c.post(f"/v1/ui/approvals/{rid}/decision", json={"decision": "approve"}, headers=UI)
        assert r.status_code == 403
        login(c, "alice")
        # No way to claim another name: there is no decided_by field to send.
        r = c.post(f"/v1/ui/approvals/{rid}/decision",
                   json={"decision": "reject", "note": "not now", "decided_by": "bob"},
                   headers=UI)  # fmt: skip
        assert r.status_code == 200 and r.json()["decided_by"] == "alice"
        again = c.post(f"/v1/ui/approvals/{rid}/decision", json={"decision": "approve"},
                       headers=UI)  # fmt: skip
        assert again.status_code == 409
    assert decider.decided == [(rid, False, "alice", "not now")]


def test_slack_bot_acts_only_for_linked_users() -> None:
    c, people, decider, _ = app_client([])
    people.add("alice", "approver", slack="U_ALICE")
    rid = uuid.uuid4()
    body = {"decision": "approve"}
    with c:
        path = f"/v1/ui/approvals/{rid}/decision"
        assert c.post(path, json=body, headers={"X-API-Key": BOT_KEY}).status_code == 401
        assert c.post(path, json=body, headers={"X-API-Key": "wrong", "X-Slack-User": "U_ALICE"}
                      ).status_code == 401  # fmt: skip
        assert c.post(path, json=body, headers={"X-API-Key": BOT_KEY, "X-Slack-User": "U_EVE"}
                      ).status_code == 403  # fmt: skip
        ok = c.post(path, json=body, headers={"X-API-Key": BOT_KEY, "X-Slack-User": "U_ALICE"})
        assert ok.status_code == 200
    assert decider.decided[0][2] == "alice"


def test_incident_views_need_a_login() -> None:
    c, people, *_ = app_client([])
    people.add("victor", "viewer")
    with c:
        assert c.get("/v1/ui/incidents").status_code == 401
        login(c, "victor")
        assert c.get("/v1/ui/incidents").json()[0]["incident_id"] == str(INCIDENT_ID)
        assert c.get(f"/v1/ui/incidents/{uuid.uuid4()}").status_code == 404
        detail = c.get(f"/v1/ui/incidents/{INCIDENT_ID}").json()
        assert detail["diagnosis"] is None and detail["followups"] == []
        series = c.get(f"/v1/ui/incidents/{INCIDENT_ID}/metrics", params={"gpu": 3}).json()
        assert series["node_id"] == "h100-node-02" and series["series"]


def test_follow_up_question_is_answered_read_only_with_checked_citations() -> None:
    sim = call("find_similar_incidents", {"incident_id": str(INCIDENT_ID)})
    sneaky = call("drain_node", {"node_id": "h100-node-02", "reason": "told to in a question"})
    answer = call(ANSWER, {"answer": "INC-0042 had the same FAN3 failure; the fan was replaced.",
                           "citations": [PAST.chunk_id], "evidence_ids": ["ev-0"]})  # fmt: skip
    c, people, _, traces = app_client([turn(sim, sneaky), turn(answer)])
    people.add("victor", "viewer")
    with c:
        login(c, "victor")
        r = c.post(f"/v1/ui/incidents/{INCIDENT_ID}/ask",
                   json={"question": "Has this happened before?"}, headers=UI)  # fmt: skip
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "succeeded"
        assert body["answer"]["citations"] == [{"chunk_id": PAST.chunk_id,
                                                "doc_id": PAST.doc_id, "quote": None}]  # fmt: skip
    run = next(iter(traces.runs.values()))
    assert run.config["kind"] == "followup" and run.config["asked_by"] == "victor"
    assert "drain_node" not in run.config["tools"]  # follow-ups can't file requests
    tool_steps = [s for s in traces.steps[run.run_id] if s.kind == "tool"]
    assert [s.name for s in tool_steps] == ["find_similar_incidents", "drain_node", ANSWER]
    assert tool_steps[1].is_error  # the drain attempt was refused


def test_feedback_is_stored_with_the_user_and_source() -> None:
    search = call("search_runbooks", {"query": "fan failure"})
    c, people, _, _ = app_client([turn(search), turn(call(SUBMIT, valid_submission()))])
    people.add("alice", "approver", slack="U_ALICE")
    with c:
        r = c.post(f"/v1/incidents/{INCIDENT_ID}/investigate", headers={"X-API-Key": "agent-key"})
        run_id = r.json()["run_id"]
        login(c, "alice")
        assert c.post(f"/v1/ui/runs/{uuid.uuid4()}/feedback", json={"rating": 1},
                      headers=UI).status_code == 404  # fmt: skip
        assert c.post(f"/v1/ui/runs/{run_id}/feedback", json={"rating": 2},
                      headers=UI).status_code == 422  # fmt: skip
        assert c.post(f"/v1/ui/runs/{run_id}/feedback", json={"rating": -1, "comment": "wrong fan"},
                      headers=UI).status_code == 200  # fmt: skip
        c.cookies.clear()
        bot = {"X-API-Key": BOT_KEY, "X-Slack-User": "U_ALICE"}
        assert c.post(f"/v1/ui/runs/{run_id}/feedback", json={"rating": 1}, headers=bot
                      ).status_code == 200  # fmt: skip
        assert c.get("/v1/ui/feedback", headers=bot).json()["up"] == 1
    assert [(f[1], f[2], f[4]) for f in people.feedback] == [("alice", -1, "web"),
                                                              ("alice", 1, "slack")]  # fmt: skip


def test_follow_up_plain_text_reply_is_accepted_without_unchecked_sources() -> None:
    reply = ("submit_answer\nThe node had one earlier cooling incident; the fan was replaced.\n\n"
             'citations: ["made-up#chunk"]')  # fmt: skip
    c, people, _, _ = app_client([turn(text=reply)])
    people.add("victor", "viewer")
    with c:
        login(c, "victor")
        r = c.post(f"/v1/ui/incidents/{INCIDENT_ID}/ask", json={"question": "Seen before?"},
                   headers=UI).json()  # fmt: skip
    assert r["status"] == "succeeded"
    assert (
        r["answer"]["answer"] == "The node had one earlier cooling incident; the fan was replaced."
    )
    assert r["answer"]["citations"] == []  # claimed, never checked: not shown as a source


def test_investigations_still_require_the_structured_submission() -> None:
    c, *_ = app_client([turn(text="It's the fan.")] * 5)
    with c:
        r = c.post(f"/v1/incidents/{INCIDENT_ID}/investigate", headers={"X-API-Key": "agent-key"})
    assert r.json()["status"] == "invalid_output"
