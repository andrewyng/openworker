WITH event_decisions AS (
    SELECT a.session_id || ':' || a.cursor AS id, a.created, a.session_id, a.cursor,
        COALESCE(e.turn_id, submitted.turn_id) AS turn_id, submitted.id IS NOT NULL AS submission_admitted,
        owner.principal AS app, operation.release_id AS release, s.data->>'coworker' AS agent,
        a.kind,
        CASE WHEN a.kind='safety_evaluation'
            THEN lower(regexp_replace(e.data->>'phase','([A-Z])','_\1','g')) ELSE 'tool_call' END AS phase,
        CASE WHEN a.kind='safety_evaluation' THEN e.data->>'check' END AS policy,
        CASE WHEN a.kind='safety_evaluation' THEN e.data->>'version' END AS policy_version,
        CASE WHEN e.data->>'policy_digest' ~ '^[a-f0-9]{64}$' THEN e.data->>'policy_digest' END AS policy_digest,
        COALESCE(e.data->>'callId',e.data->>'call_id',e.data#>>'{call,call_id}') AS call_id,
        COALESCE(e.data->>'tool',e.data#>>'{call,name}') AS tool,
        COALESCE(e.data->>'interaction_id',e.data->>'id') AS interaction,
        CASE WHEN a.kind='safety_evaluation' THEN 'guardrail'
             WHEN e.data#>>'{review,verdict}' IN ('allow','deny') THEN 'reviewer'
             ELSE e.data->>'origin' END AS source,
        CASE WHEN a.kind='safety_evaluation' THEN
            CASE WHEN e.data->>'outcome'='allow'
                    AND jsonb_typeof(e.data->'matchCount')='number' AND e.data->'matchCount'>'0'::jsonb
                THEN 'matched' ELSE e.data->>'outcome' END
            WHEN e.data#>>'{review,verdict}'='allow' THEN 'approved'
            WHEN e.data#>>'{review,verdict}'='deny' THEN 'denied'
            WHEN e.data->>'origin' IN ('human','app_policy','session_grant','workspace_read_grant','hard_policy','bypass')
               AND (e.data->>'response' IN ('once','session') OR e.data->>'decision'='approved') THEN 'approved'
            WHEN e.data->>'origin' IN ('human','app_policy','session_grant','workspace_read_grant','hard_policy','bypass')
               AND (e.data->>'response'='deny' OR e.data->>'decision'='denied') THEN 'denied'
            WHEN e.data->>'origin'='app_policy' AND e.data->>'response' IS NULL THEN 'approved'
        END AS outcome
    FROM audit a
    JOIN events e USING(session_id,cursor)
    JOIN sessions s ON s.id=a.session_id
    LEFT JOIN api_session_owners owner ON owner.session_id=a.session_id
    LEFT JOIN service_operations operation ON operation.session_id=a.session_id
    LEFT JOIN submissions submitted ON e.turn_id IS NULL AND submitted.session_id=a.session_id
        AND submitted.id=e.data->>'submission_id'
    WHERE a.created >= TIMESTAMPTZ 'epoch' + $4::bigint * INTERVAL '1 millisecond'
      AND a.created < TIMESTAMPTZ 'epoch' + $5::bigint * INTERVAL '1 millisecond'
      AND (jsonb_typeof(s.data->'compliance') IS DISTINCT FROM 'object' OR
          a.created >= now()-COALESCE((s.data->'compliance'->>'retentionDays')::bigint,3650)*INTERVAL '1 day')
      AND ($1::boolean OR owner.principal=ANY($2::text[]) OR a.session_id=ANY($3::text[]))
      AND ($6::text IS NULL OR owner.principal=$6)
      AND (a.kind='safety_evaluation'
           OR (a.kind='interaction_resolved' AND e.data->>'kind'='approval'))
), ingress_decisions AS (
    SELECT 'ingress:' || a.id AS id, a.created, NULL::text AS session_id, NULL::bigint AS cursor,
        NULL::text AS turn_id, false AS submission_admitted, a.principal AS app, NULL::text AS release, NULL::text AS agent,
        'safety_evaluation' AS kind, 'user_input' AS phase, split_part(a.target,':',2) AS policy,
        NULL::text AS policy_version,NULL::text AS policy_digest,NULL::text AS call_id,NULL::text AS tool,NULL::text AS interaction,
        'guardrail' AS source,split_part(a.target,':',3) AS outcome
    FROM api_security_audit a
    WHERE a.created >= TIMESTAMPTZ 'epoch' + $4::bigint * INTERVAL '1 millisecond'
      AND a.created < TIMESTAMPTZ 'epoch' + $5::bigint * INTERVAL '1 millisecond'
      AND a.action='turn.submit' AND a.session_id IS NULL
      AND a.target ~ '^guardrail:[A-Za-z0-9._-]{1,128}:(blocked|redacted|unavailable)$'
      AND ($1 OR a.principal=ANY($2))
      AND ($6 IS NULL OR a.principal=$6)
), activity AS (
    SELECT * FROM event_decisions
    UNION ALL
    SELECT * FROM ingress_decisions
), filtered AS (
    SELECT * FROM activity
    WHERE outcome IN ('approved','denied','blocked','redacted','unavailable','matched')
      AND ($7::text IS NULL OR COALESCE(release,agent)=$7)
      AND ($8::text IS NULL OR outcome=$8)
      AND ($9::text IS NULL OR policy=$9)
      AND ($10::text IS NULL OR strpos(lower(concat_ws(' ',id,app,release,agent,policy,tool,phase,source,outcome)),$10)>0)
)
