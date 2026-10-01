-- Security review of A5: the self-approval check in 007 was case-sensitive, so a decider named
-- "Agent:x" or " agent" passed it. Compare names trimmed and lower-cased.

CREATE OR REPLACE FUNCTION approval_requests_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
    IF NEW.decided_by IS NULL
       OR lower(btrim(NEW.decided_by)) LIKE 'agent%'
       OR lower(btrim(NEW.decided_by)) = lower(btrim(OLD.requested_by)) THEN
        RAISE EXCEPTION 'an approval must be decided by a person other than the requester';
    END IF;
    NEW.decided_at := now();
    RETURN NEW;
END
$$;
