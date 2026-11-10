CREATE TABLE IF NOT EXISTS safety_observers (
 session_id TEXT NOT NULL,cursor BIGINT NOT NULL,hook_name TEXT NOT NULL,hook_version TEXT NOT NULL,
 hook JSONB NOT NULL,record JSONB NOT NULL,turn_id TEXT,next_ms BIGINT NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(session_id,cursor,hook_name,hook_version));
CREATE OR REPLACE FUNCTION ow_observer_capture() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE session_data JSONB; facts JSONB; h JSONB;
BEGIN
 IF NEW.kind IN ('assistant_delta','tool_progress','agent_run_state','observer_delivery') THEN RETURN NEW; END IF;
 SELECT data INTO session_data FROM sessions WHERE id=NEW.session_id;
  SELECT coalesce(jsonb_object_agg(key,value),'{}'::jsonb) INTO facts
  FROM jsonb_each(NEW.data) WHERE
    (key IN ('call_id','callId','tool','interaction_id','attempt_id','submission_id','evaluationId','check','version','phase','purpose','outcome','code','status','name','origin','approval_origin','grant_id','policy_digest','resource_policy_digest','bundle_digest')
      AND jsonb_typeof(value)='string' AND (value#>>'{}') ~ '^[A-Za-z0-9._:/-]{1,160}$')
    OR (key IN ('executed','outputBlocked','result_held','guarded','automatic_replay') AND jsonb_typeof(value)='boolean')
    OR (key IN ('durationMs','matchCount','retry','max_retries','attempt','output_tokens','reserved_output_tokens','reported_output_tokens')
      AND jsonb_typeof(value)='number' AND (value#>>'{}') ~ '^[0-9]{1,19}$');

 FOR h IN SELECT value FROM jsonb_array_elements(coalesce(session_data->'safety'->'hooks','[]'::jsonb)||CASE WHEN session_data->'turn'->>'id'=NEW.turn_id THEN coalesce(session_data->'turn'->'safety'->'hooks','[]'::jsonb) ELSE '[]'::jsonb END) WHERE value->>'when'='onEvent' LOOP
 INSERT INTO safety_observers(session_id,cursor,hook_name,hook_version,hook,record,turn_id)
 VALUES(NEW.session_id,NEW.cursor,h->>'name',h->>'version',h,jsonb_build_object('schemaVersion',1,'kind',NEW.kind,'agent',session_data->'coworker','facts',facts),NEW.turn_id) ON CONFLICT DO NOTHING;
 END LOOP;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS safety_observer_capture ON events;
CREATE TRIGGER safety_observer_capture AFTER INSERT ON events FOR EACH ROW EXECUTE FUNCTION ow_observer_capture();
