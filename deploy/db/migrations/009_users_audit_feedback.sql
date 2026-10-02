-- A7: people. Accounts with roles (approvals are decided by a logged-in user, not a typed name),
-- server-side sessions, an append-only audit log of every approval event, feedback on
-- diagnoses, and which incidents the Slack bot has already posted.

CREATE TABLE users (
    username       text        PRIMARY KEY CHECK (username ~ '^[a-z0-9][a-z0-9._-]{1,63}$'),
    display_name   text        NOT NULL,
    role           text        NOT NULL CHECK (role IN ('viewer', 'approver', 'admin')),
    password_hash  text        NOT NULL,   -- scrypt; see agent.users
    slack_user_id  text        UNIQUE,     -- maps Slack actions to this account
    disabled       boolean     NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now(),
    -- Agents never get accounts: decisions must come from people (see approval guard, 007/008).
    CHECK (lower(username) NOT LIKE 'agent%')
);

-- Only a hash of the session token is stored, so a database read can't hijack sessions.
CREATE TABLE sessions (
    token_hash   text        PRIMARY KEY,
    username     text        NOT NULL REFERENCES users (username) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);
CREATE INDEX sessions_expiry ON sessions (expires_at);

-- Append-only: every request and decision, written by a trigger so no code path can skip it.
CREATE TABLE approval_audit (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    request_id   uuid        NOT NULL,
    event        text        NOT NULL CHECK (event IN ('requested', 'approved', 'rejected')),
    actor        text        NOT NULL,
    action       text        NOT NULL,
    node_id      text        NOT NULL,
    gpu_index    smallint,
    note         text
);
CREATE INDEX approval_audit_request ON approval_audit (request_id, at);
CREATE INDEX approval_audit_at ON approval_audit (at DESC);

CREATE FUNCTION approval_requests_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO approval_audit (request_id, event, actor, action, node_id, gpu_index, note)
        VALUES (NEW.request_id, 'requested', NEW.requested_by, NEW.action, NEW.node_id,
                NEW.gpu_index, NEW.reason);
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO approval_audit (request_id, event, actor, action, node_id, gpu_index, note)
        VALUES (NEW.request_id, NEW.status, NEW.decided_by, NEW.action, NEW.node_id,
                NEW.gpu_index, NEW.decision_note);
    END IF;
    RETURN NULL;
END
$$;
CREATE TRIGGER approval_requests_audit AFTER INSERT OR UPDATE ON approval_requests
    FOR EACH ROW EXECUTE FUNCTION approval_requests_audit();

CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END
$$;
CREATE TRIGGER approval_audit_append_only BEFORE UPDATE OR DELETE ON approval_audit
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- Existing requests get their history backfilled once.
INSERT INTO approval_audit (at, request_id, event, actor, action, node_id, gpu_index, note)
SELECT created_at, request_id, 'requested', requested_by, action, node_id, gpu_index, reason
FROM approval_requests;
INSERT INTO approval_audit (at, request_id, event, actor, action, node_id, gpu_index, note)
SELECT decided_at, request_id, status, decided_by, action, node_id, gpu_index, decision_note
FROM approval_requests WHERE status <> 'pending';

-- One rating per person per run; changing your mind updates it.
CREATE TABLE feedback (
    run_id       uuid        NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    username     text        NOT NULL REFERENCES users (username),
    rating       smallint    NOT NULL CHECK (rating IN (-1, 1)),
    comment      text        CHECK (length(comment) <= 2000),
    source       text        NOT NULL CHECK (source IN ('web', 'slack')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, username)
);

CREATE TABLE slack_posts (
    incident_id  uuid        PRIMARY KEY,
    channel      text        NOT NULL,
    ts           text        NOT NULL,    -- Slack message timestamp: the thread root
    run_id       uuid,                    -- diagnosis posted in the thread, if any
    posted_at    timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE ON users TO incident_agent;
GRANT SELECT, INSERT, DELETE ON sessions TO incident_agent;
GRANT SELECT ON approval_audit TO incident_agent, approval_service, grafana_reader;
-- The audit trigger runs as whoever changes approval_requests.
GRANT INSERT ON approval_audit TO incident_agent, approval_service;
GRANT SELECT, INSERT, UPDATE ON feedback TO incident_agent;
GRANT SELECT ON feedback TO grafana_reader;
GRANT SELECT, INSERT, UPDATE ON slack_posts TO incident_agent;
