CREATE TABLE IF NOT EXISTS compliance_delivery(session_id TEXT NOT NULL,sink TEXT NOT NULL,data JSONB NOT NULL,PRIMARY KEY(session_id,sink));
CREATE TABLE IF NOT EXISTS compliance_events (
  session_id TEXT NOT NULL, source_cursor BIGINT NOT NULL, turn_id TEXT,
  created_ms BIGINT NOT NULL, kind TEXT NOT NULL, record JSONB NOT NULL,
  PRIMARY KEY(session_id,source_cursor)
);
CREATE INDEX IF NOT EXISTS compliance_kind ON compliance_events(session_id,kind,source_cursor);

CREATE OR REPLACE FUNCTION ow_compliance_capture() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE session_data JSONB; facts JSONB; principal TEXT;
BEGIN
  IF NEW.kind IN ('assistant_delta','tool_progress','agent_run_state') THEN RETURN NEW; END IF;
  SELECT data INTO session_data FROM sessions WHERE id=NEW.session_id;
  IF jsonb_typeof(session_data->'compliance') IS DISTINCT FROM 'object' THEN RETURN NEW; END IF;
  SELECT coalesce(jsonb_object_agg(key,value),'{}'::jsonb) INTO facts
  FROM jsonb_each(NEW.data) WHERE
    (key IN ('call_id','callId','tool','interaction_id','attempt_id','submission_id','evaluationId','check','version','phase','purpose','outcome','code','status','name','origin','approval_origin','grant_id','policy_digest','resource_policy_digest','bundle_digest')
      AND jsonb_typeof(value)='string' AND (value#>>'{}') ~ '^[A-Za-z0-9._:/-]{1,160}$')
    OR (key IN ('executed','outputBlocked','result_held','guarded','automatic_replay') AND jsonb_typeof(value)='boolean')
    OR (key IN ('durationMs','matchCount','retry','max_retries','attempt','output_tokens','reserved_output_tokens','reported_output_tokens')
      AND jsonb_typeof(value)='number' AND (value#>>'{}') ~ '^[0-9]{1,19}$');
  SELECT o.principal INTO principal FROM api_session_owners o WHERE o.session_id=NEW.session_id;
  INSERT INTO compliance_events VALUES(NEW.session_id,NEW.cursor,NEW.turn_id,
    (extract(epoch FROM clock_timestamp())*1000)::bigint,NEW.kind,
    jsonb_build_object('schemaVersion',1,'kind',NEW.kind,'agent',session_data->'coworker',
      'team',session_data->'agent_team'->'team_id','instance',session_data->'agent_team'->'agent_id',
      'sessionOwner',principal,'facts',facts));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS compliance_capture ON events;
CREATE TRIGGER compliance_capture AFTER INSERT ON events
FOR EACH ROW EXECUTE FUNCTION ow_compliance_capture();

CREATE OR REPLACE FUNCTION ow_compliance_backpressure() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ceiling BIGINT; pending BIGINT;
BEGIN
 IF NEW.kind NOT IN ('turn_accepted','model_request','tool_started') THEN RETURN NEW; END IF;
 SELECT coalesce((data->'compliance'->>'maxPendingEvents')::bigint,10000) INTO ceiling FROM sessions WHERE id=NEW.session_id AND jsonb_typeof(data->'compliance')='object';
 IF ceiling IS NULL THEN RETURN NEW; END IF;
 SELECT count(*) INTO pending FROM compliance_events e WHERE e.session_id=NEW.session_id AND EXISTS(SELECT 1 FROM compliance_delivery d WHERE d.session_id=e.session_id AND (d.data->>'cursor')::bigint<e.source_cursor);
 IF pending>=ceiling THEN RAISE EXCEPTION 'Compliance export backlog full'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS compliance_backpressure ON events;
CREATE TRIGGER compliance_backpressure BEFORE INSERT ON events FOR EACH ROW EXECUTE FUNCTION ow_compliance_backpressure();
