-- Investigation agent (A5): run traces, and approval requests for state-changing actions.
--
-- agent_runs / agent_steps: one row per investigation and one per model call or tool call, with
-- tokens, cost and latency, so the A6 eval can score runs straight from the database. The
-- diagnosis lives on the run rather than in incidents.record: the detector owns incident rows and
-- rewrites them on every update, which would silently drop an agent-written field.
--
-- approval_requests: the agent can ask for a drain or GPU reset but never perform or approve one.
-- Two roles enforce that split: incident_agent may only INSERT requests; approval_service may
-- only UPDATE the decision columns. A trigger makes a decision final and keeps the request itself
-- immutable, whichever role (or bug) tries otherwise.

CREATE TABLE agent_runs (
    run_id         uuid        PRIMARY KEY,
    incident_id    uuid        NOT NULL,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text        NOT NULL DEFAULT 'running' CHECK (status IN (
                       'running', 'succeeded', 'invalid_output', 'budget_exceeded', 'timeout',
                       'refused', 'error')),
    provider       text        NOT NULL,
    model          text        NOT NULL,
    steps          integer     NOT NULL DEFAULT 0,
    tool_calls     integer     NOT NULL DEFAULT 0,
    input_tokens   integer     NOT NULL DEFAULT 0,
    output_tokens  integer     NOT NULL DEFAULT 0,
    cost_usd       numeric(12, 6) NOT NULL DEFAULT 0,
    latency_ms     integer,
    diagnosis      jsonb,
    error          text,
    config         jsonb       NOT NULL DEFAULT '{}'
);
CREATE INDEX agent_runs_incident ON agent_runs (incident_id, started_at DESC);
CREATE INDEX agent_runs_started ON agent_runs (started_at DESC);

CREATE TABLE agent_steps (
    run_id         uuid        NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    seq            integer     NOT NULL,
    kind           text        NOT NULL CHECK (kind IN ('model', 'tool')),
    name           text        NOT NULL,
    started_at     timestamptz NOT NULL,
    latency_ms     integer     NOT NULL,
    input          jsonb,
    output         jsonb,
    is_error       boolean     NOT NULL DEFAULT false,
    input_tokens   integer     NOT NULL DEFAULT 0,
    output_tokens  integer     NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE approval_requests (
    request_id     uuid        PRIMARY KEY,
    created_at     timestamptz NOT NULL DEFAULT now(),
    action         text        NOT NULL CHECK (action IN ('drain_node', 'reset_gpu')),
    node_id        text        NOT NULL,
    gpu_index      smallint,
    reason         text        NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
    incident_id    uuid,
    run_id         uuid,
    requested_by   text        NOT NULL,
    status         text        NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by     text,
    decided_at     timestamptz,
    decision_note  text,
    CHECK (action <> 'reset_gpu' OR gpu_index IS NOT NULL),
    CHECK ((status = 'pending') = (decided_by IS NULL))
);
-- One open request per action and target: asking twice returns the existing request.
CREATE UNIQUE INDEX approval_requests_one_pending
    ON approval_requests (action, node_id, coalesce(gpu_index, -1)) WHERE status = 'pending';
CREATE INDEX approval_requests_status ON approval_requests (status, created_at DESC);

CREATE FUNCTION approval_requests_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending' THEN
            RAISE EXCEPTION 'approval requests must be created pending';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'approval request % was already %', OLD.request_id, OLD.status;
    END IF;
    IF (NEW.request_id, NEW.created_at, NEW.action, NEW.node_id, NEW.gpu_index, NEW.reason,
        NEW.incident_id, NEW.run_id, NEW.requested_by)
       IS DISTINCT FROM
       (OLD.request_id, OLD.created_at, OLD.action, OLD.node_id, OLD.gpu_index, OLD.reason,
        OLD.incident_id, OLD.run_id, OLD.requested_by) THEN
        RAISE EXCEPTION 'only the decision of an approval request can change';
    END IF;
    IF NEW.decided_by IS NULL OR NEW.decided_by LIKE 'agent%' OR NEW.decided_by = OLD.requested_by THEN
        RAISE EXCEPTION 'an approval must be decided by a person other than the requester';
    END IF;
    NEW.decided_at := now();
    RETURN NEW;
END
$$;
CREATE TRIGGER approval_requests_guard BEFORE INSERT OR UPDATE ON approval_requests
    FOR EACH ROW EXECUTE FUNCTION approval_requests_guard();

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'incident_agent') THEN
        CREATE ROLE incident_agent NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'approval_service') THEN
        CREATE ROLE approval_service NOLOGIN;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO incident_agent, approval_service',
                   current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO incident_agent, approval_service;

-- The agent reads telemetry and incidents, writes its own traces, and can only file requests.
GRANT SELECT ON gpu_metrics, xid_events, bmc_log, gpu_metrics_1m, incidents TO incident_agent;
GRANT SELECT, INSERT, UPDATE ON agent_runs TO incident_agent;
GRANT SELECT, INSERT ON agent_steps TO incident_agent;
GRANT SELECT, INSERT ON approval_requests TO incident_agent;
ALTER ROLE incident_agent SET statement_timeout = '30s';

-- Approvers see requests and record decisions; nothing else.
GRANT SELECT ON approval_requests TO approval_service;
GRANT UPDATE (status, decided_by, decided_at, decision_note) ON approval_requests
    TO approval_service;
ALTER ROLE approval_service SET statement_timeout = '10s';

GRANT SELECT ON agent_runs, agent_steps, approval_requests TO grafana_reader;
