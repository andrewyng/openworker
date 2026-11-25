//! Metadata-only evidence from the execution and security journals.
use crate::{
    auth::{self, Action, Identity, Scope},
    submissions::validate_id,
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use openworker_runtime::compliance::delivery::Checkpoint;
use postgres::{types::ToSql, Transaction};
use serde::Deserialize;
use serde_json::{json, Value};

const DAY: i64 = 86_400_000;
const EXPORT_LIMIT: i64 = 2000;
const DECISIONS: &str = include_str!("compliance_decisions.sql");

#[cfg(feature = "compliance")]
pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    tx.batch_execute("
        CREATE INDEX IF NOT EXISTS audit_compliance_approvals ON audit(created DESC,session_id,cursor)
            WHERE kind='interaction_resolved';
        CREATE INDEX IF NOT EXISTS audit_compliance_turns ON audit(created DESC,session_id)
            WHERE kind='turn_started';
        CREATE INDEX IF NOT EXISTS events_compliance_tool ON events(session_id,turn_id,(COALESCE(data->>'call_id',data#>>'{call,call_id}')),cursor)
            WHERE kind IN ('tool_started','tool_finished','interaction_resolved');
        CREATE INDEX IF NOT EXISTS api_security_audit_compliance_time ON api_security_audit(created DESC,id DESC);
        CREATE INDEX IF NOT EXISTS api_session_owners_principal ON api_session_owners(principal,session_id);
        CREATE TABLE IF NOT EXISTS compliance_evidence_deliveries (
            id TEXT PRIMARY KEY, actor TEXT NOT NULL, sink TEXT NOT NULL, binding TEXT NOT NULL,
            selection JSONB NOT NULL, record_count BIGINT NOT NULL, payload JSONB,
            checkpoint JSONB NOT NULL, created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS compliance_evidence_pending ON compliance_evidence_deliveries(sink,created)
            WHERE payload IS NOT NULL;
    ")?;
    Ok(())
}

#[derive(Default, Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
pub struct Search {
    pub app: Option<String>,
    pub agent: Option<String>,
    pub outcome: Option<String>,
    pub policy: Option<String>,
    pub q: Option<String>,
    pub from_ms: Option<i64>,
    pub until_ms: Option<i64>,
    pub before_us: Option<i64>,
    pub before_id: Option<String>,
    pub limit: Option<u16>,
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Overview {
    pub from_ms: Option<i64>,
    pub until_ms: Option<i64>,
    pub after: Option<String>,
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuditSearch {
    pub from_ms: Option<i64>,
    pub until_ms: Option<i64>,
    pub app: Option<String>,
    pub q: Option<String>,
    pub before: Option<i64>,
    pub limit: Option<u16>,
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoverageSearch {
    pub release: Option<String>,
    pub from_ms: Option<i64>,
    pub until_ms: Option<i64>,
}

struct Window {
    from: i64,
    until: i64,
    limit: i64,
}

impl Search {
    fn window(&self) -> Result<Window> {
        let now = openworker_runtime::store::now_ms() as i64;
        let until = self.until_ms.unwrap_or(now + 1);
        let from = self.from_ms.unwrap_or(until.saturating_sub(7 * DAY));
        let limit = i64::from(self.limit.unwrap_or(25));
        ensure!(
            (1..=100).contains(&limit)
                && (0..until).contains(&from)
                && until <= now + 60_000
                && until - from <= 31 * DAY
                && self.before_us.is_some() == self.before_id.is_some()
                && self
                    .before_us
                    .is_none_or(|v| (from * 1000..until * 1000).contains(&v)),
            SubmissionError::InvalidRequest
        );
        for value in [&self.app, &self.agent, &self.policy].into_iter().flatten() {
            validate_id(value, 128)?;
        }
        if let Some(id) = &self.before_id {
            let (session, cursor) = id.rsplit_once(':').ok_or(SubmissionError::InvalidRequest)?;
            validate_id(session, 128)?;
            ensure!(
                cursor.parse::<i64>().is_ok_and(|n| n > 0),
                SubmissionError::InvalidRequest
            );
        }
        ensure!(
            self.q
                .as_ref()
                .is_none_or(|s| s.len() <= 160 && !s.chars().any(char::is_control))
                && self.outcome.as_deref().is_none_or(|s| matches!(
                    s,
                    "approved"
                        | "denied"
                        | "blocked"
                        | "redacted"
                        | "unavailable"
                        | "matched"
                        | "alerted"
                        | "would_block"
                        | "shadow_unavailable"
                )),
            SubmissionError::InvalidRequest
        );
        Ok(Window { from, until, limit })
    }
}

struct Access {
    all: bool,
    apps: Vec<String>,
    sessions: Vec<String>,
}

impl Access {
    fn for_identity(identity: &Identity) -> Self {
        let mut access = Self {
            all: false,
            apps: Vec::new(),
            sessions: Vec::new(),
        };
        match &identity.scope {
            Scope::All => access.all = true,
            Scope::Owned => access.apps.push(identity.principal.clone()),
            Scope::Sessions { ids } => access.sessions.clone_from(ids),
            Scope::Apps { principals } => access.apps.clone_from(principals),
        }
        access
    }

    fn params<'a>(
        &'a self,
        q: &'a Search,
        w: &'a Window,
        term: &'a Option<String>,
    ) -> [&'a (dyn ToSql + Sync); 10] {
        [
            &self.all,
            &self.apps,
            &self.sessions,
            &w.from,
            &w.until,
            &q.app,
            &q.agent,
            &q.outcome,
            &q.policy,
            term,
        ]
    }
}

// Only affirmative durable evidence is reported as a dispatch or result. An
// approval alone cannot prove execution, and a later approval for the same
// call cannot provide proof for an earlier decision.
const DECISION_PAGE: &str = "
, page AS (
    SELECT * FROM filtered
    WHERE ($11::bigint IS NULL OR (created,id)<(TIMESTAMPTZ 'epoch'+$11*INTERVAL '1 microsecond',$12))
    ORDER BY created DESC,id DESC LIMIT $13
)
SELECT (extract(epoch FROM p.created)*1000000)::bigint, jsonb_build_object(
    'id',p.id,'session_id',p.session_id,'turn_id',p.turn_id,'app',p.app,'agent',p.agent,
    'release',p.release,'phase',left(p.phase,40),'policy',left(p.policy,128),
    'policy_version',left(p.policy_version,80),'policy_digest',p.policy_digest,'tool',left(p.tool,128),
    'source',p.source,'outcome',p.outcome,'occurred_ms',floor(extract(epoch FROM p.created)*1000)::bigint,
    'effect',CASE
      WHEN p.outcome IN ('would_block','alerted','shadow_unavailable') THEN 'observation_only'
      WHEN p.phase IN ('model_output','tool_result') AND p.outcome IN ('blocked','unavailable')
        THEN CASE WHEN p.phase='tool_result' THEN 'result_withheld' ELSE 'response_withheld' END
      WHEN p.phase='user_input' AND p.outcome='blocked' THEN 'request_blocked'
      WHEN p.phase='model_input' AND p.outcome='blocked' THEN 'model_blocked'
      WHEN p.kind='safety_evaluation' AND p.phase='tool_call' AND p.outcome='blocked' THEN 'tool_blocked'
      WHEN evidence.kind='tool_finished' AND evidence.executed='true' AND evidence.held='true' THEN 'result_withheld'
      WHEN evidence.kind='tool_finished' AND evidence.executed='true' THEN 'tool_result_recorded'
      WHEN evidence.kind='tool_finished' AND evidence.executed='false' THEN 'tool_blocked'
      WHEN evidence.kind='tool_started' THEN 'tool_started'
      WHEN p.phase='user_input' AND p.outcome='redacted' AND p.submission_admitted THEN 'turn_admitted'
      ELSE 'unconfirmed' END
)
FROM page p
LEFT JOIN LATERAL (
    SELECT e.kind,e.data->>'executed' AS executed,e.data->>'outputBlocked' AS held
    FROM events e
    WHERE p.call_id IS NOT NULL AND p.turn_id IS NOT NULL AND e.session_id=p.session_id
      AND e.turn_id=p.turn_id AND COALESCE(e.data->>'call_id',e.data#>>'{call,call_id}')=p.call_id
      AND e.cursor > p.cursor AND e.kind IN ('tool_started','tool_finished','interaction_resolved')
      AND (e.kind='interaction_resolved' OR p.interaction IS NULL
           OR e.data->>'interaction_id' IS NULL OR e.data->>'interaction_id'=p.interaction)
    ORDER BY e.cursor LIMIT 1
) first_effect ON true
LEFT JOIN LATERAL (
    SELECT e.kind,e.data->>'executed' AS executed,e.data->>'outputBlocked' AS held
    FROM events e
    WHERE first_effect.kind='tool_started' AND e.session_id=p.session_id AND e.turn_id=p.turn_id
      AND COALESCE(e.data->>'call_id',e.data#>>'{call,call_id}')=p.call_id AND e.cursor > p.cursor
      AND e.kind IN ('tool_finished','interaction_resolved')
      AND (e.kind='interaction_resolved' OR p.interaction IS NULL
           OR e.data->>'interaction_id' IS NULL OR e.data->>'interaction_id'=p.interaction)
    ORDER BY e.cursor LIMIT 1
) result ON true
CROSS JOIN LATERAL (
    SELECT CASE WHEN result.kind='tool_finished' THEN result.kind
                WHEN first_effect.kind<>'interaction_resolved' THEN first_effect.kind END AS kind,
           CASE WHEN result.kind='tool_finished' THEN result.executed ELSE first_effect.executed END AS executed,
           CASE WHEN result.kind='tool_finished' THEN result.held ELSE first_effect.held END AS held
) evidence
ORDER BY p.created DESC,p.id DESC";

const AGENT_OVERVIEW: &str = "
, counts AS (
    SELECT app,COALESCE(release,agent) AS name,max(agent) AS agent,max(release) AS release,
        count(*) AS decisions,
        count(*) FILTER (WHERE outcome IN ('blocked','denied')) AS blocked,
        count(*) FILTER (WHERE outcome='redacted') AS redacted,
        count(*) FILTER (WHERE outcome='would_block') AS would_block,
        count(*) FILTER (WHERE outcome IN ('unavailable','shadow_unavailable')) AS unavailable,
        0::bigint AS turns
    FROM filtered GROUP BY app,COALESCE(release,agent)
    UNION ALL
    SELECT owner.principal,COALESCE(op.release_id,s.data->>'coworker'),max(s.data->>'coworker'),max(op.release_id),
        0,0,0,0,0,count(*)
    FROM audit a
    JOIN sessions s ON s.id=a.session_id
    LEFT JOIN api_session_owners owner ON owner.session_id=s.id
    LEFT JOIN service_operations op ON op.session_id=s.id
    WHERE a.kind='turn_started'
      AND a.created >= TIMESTAMPTZ 'epoch'+$4::bigint*INTERVAL '1 millisecond'
      AND a.created < TIMESTAMPTZ 'epoch'+$5::bigint*INTERVAL '1 millisecond'
      AND ($1 OR owner.principal=ANY($2) OR s.id=ANY($3))
    GROUP BY owner.principal,COALESCE(op.release_id,s.data->>'coworker')
), grouped AS (
    SELECT COALESCE(app,'') || ':' || COALESCE(name,'') AS key, app,
        max(agent) AS agent,max(release) AS release,sum(decisions)::bigint AS decisions,
        sum(blocked)::bigint AS blocked,sum(redacted)::bigint AS redacted,
        sum(unavailable)::bigint AS unavailable,sum(would_block)::bigint AS would_block,sum(turns)::bigint AS turns
    FROM counts GROUP BY app,name
)
SELECT key, jsonb_build_object('app',app,'agent',agent,'release',release,'decisions',decisions,
    'blocked',blocked,'redacted',redacted,'unavailable',unavailable,'would_block',would_block,'turns',turns)
FROM grouped WHERE $11::text IS NULL OR key>$11 ORDER BY key LIMIT 51";

const ADMIN_AUDIT: &str = "
SELECT a.id,jsonb_build_object(
    'id',a.id,'actor',a.principal,'app',scope.app,
    'target',CASE WHEN a.action IN ('compliance.evidence.download','compliance.evidence.forward') THEN NULL ELSE left(a.target,128) END,
    'action',a.action,'outcome',a.outcome,'occurred_ms',floor(extract(epoch FROM a.created)*1000)::bigint
)
FROM api_security_audit a
LEFT JOIN api_session_owners o ON o.session_id=a.session_id
LEFT JOIN api_credentials c ON c.id=a.target AND a.action LIKE 'credential.%'
CROSS JOIN LATERAL (
    SELECT COALESCE(o.principal,
        CASE WHEN c.spec->>'role'='app' THEN c.spec->>'principal' END,
        CASE WHEN a.action IN ('compliance.evidence.download','compliance.evidence.forward') AND left(a.target,4)='app_' THEN a.target END) AS app
) scope
WHERE a.created >= TIMESTAMPTZ 'epoch'+$4::bigint*INTERVAL '1 millisecond'
  AND a.created < TIMESTAMPTZ 'epoch'+$5::bigint*INTERVAL '1 millisecond'
  AND ($1::boolean OR scope.app=ANY($2::text[]) OR a.session_id=ANY($3::text[])
       OR (a.action='compliance.evidence.download' AND a.principal=$6))
  AND ($7::text IS NULL OR scope.app=$7)
  AND ($8::text IS NULL OR strpos(lower(concat_ws(' ',a.action,a.principal,scope.app,a.outcome,a.target)),$8)>0)
  AND ($9::bigint IS NULL OR a.id<$9)
  AND (a.action IN ('service.configure','release.publish','release.activate','compliance.evidence.download','compliance.evidence.forward','compliance.content.read')
       OR a.action LIKE 'credential.%')
ORDER BY a.id DESC LIMIT $10";

fn safe(mut value: Value) -> Value {
    // These fields are names/identifiers, not arguments, findings or transcripts.
    for key in [
        "agent",
        "app",
        "policy",
        "policy_version",
        "tool",
        "actor",
        "target",
    ] {
        if let Some(s) = value[key].as_str() {
            value[key] = json!(openworker_runtime::telemetry::redact(s));
        }
    }
    value
}

fn decisions(
    tx: &mut Transaction<'_>,
    access: &Access,
    q: &Search,
    w: &Window,
    size: i64,
) -> Result<Vec<(i64, Value)>> {
    let term =
        q.q.as_ref()
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty());
    let mut args = access.params(q, w, &term).to_vec();
    args.extend([&q.before_us as &(dyn ToSql + Sync), &q.before_id, &size]);
    Ok(tx
        .query(&format!("{DECISIONS}{DECISION_PAGE}"), &args)?
        .into_iter()
        .map(|r| (r.get(0), safe(r.get(1))))
        .collect())
}

fn page(mut records: Vec<(i64, Value)>, limit: i64) -> Value {
    let more = records.len() > limit as usize;
    records.truncate(limit as usize);
    let next = more
        .then(|| {
            records
                .last()
                .map(|(time, row)| json!({"before_us":time,"before_id":row["id"]}))
        })
        .flatten();
    json!({"records":records.into_iter().map(|(_, value)|value).collect::<Vec<_>>(),"next":next})
}

fn authorized(
    tx: &mut Transaction<'_>,
    context: Option<&auth::Context>,
    action: Action,
) -> Result<Identity> {
    ensure!(cfg!(feature = "compliance"), SubmissionError::NotFound);
    Ok(auth::guard(tx, context, action, None, None)?.ok_or(auth::Error::Unauthorized)?)
}

fn visible_apps(tx: &mut Transaction<'_>, access: &Access) -> Result<Vec<String>> {
    Ok(tx.query(
        "SELECT principal FROM service_app_grants WHERE $1 OR principal=ANY($2::text[])
         UNION SELECT principal FROM api_session_owners WHERE session_id=ANY($3::text[]) ORDER BY principal",
        &[&access.all,&access.apps,&access.sessions]
    )?.iter().map(|row| row.get::<_,String>(0)).filter(|name| auth::is_app_principal(name)).collect())
}

fn evidence(
    tx: &mut Transaction<'_>,
    identity: &Identity,
    q: &Search,
    window: &Window,
) -> Result<Value> {
    let rows = decisions(
        tx,
        &Access::for_identity(identity),
        q,
        window,
        EXPORT_LIMIT + 1,
    )?;
    ensure!(rows.len() <= EXPORT_LIMIT as usize, SubmissionError::Busy);
    let records = rows
        .into_iter()
        .map(|(_, mut record)| {
            record["schema_version"] = json!(1);
            record
        })
        .collect::<Vec<_>>();
    Ok(json!(records))
}

fn export_window(q: &Search, request: &str) -> Result<Window> {
    validate_id(request, 100)?;
    ensure!(
        q.before_id.is_none() && q.limit.is_none(),
        SubmissionError::InvalidRequest
    );
    q.window()
}

impl PostgresStore {
    /// Evidence of completed or unavailable checks, including clean passes.
    /// This is a journal summary. Current policy is resolved by the service.
    pub fn compliance_coverage(&self, q: CoverageSearch) -> Result<Value> {
        let window = Search {
            from_ms: q.from_ms,
            until_ms: q.until_ms,
            ..Default::default()
        }
        .window()?;
        if let Some(release) = &q.release {
            crate::service_catalog::revision(release)?;
        }
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let identity = authorized(&mut tx, db.auth.as_ref(), Action::ComplianceRead)?;
            ensure!(identity.role == auth::Role::Administrator && identity.scope == Scope::All, auth::Error::Forbidden);
            let rows = tx.query(
                "WITH checks AS (
                   SELECT o.release_id, left(s.data->>'coworker',128) AS agent,
                          left(e.data->>'check',128) AS policy, left(e.data->>'version',80) AS version,
                          e.data->>'phase' AS phase, e.data->>'outcome' AS outcome, a.created
                   FROM audit a
                   JOIN events e USING(session_id,cursor)
                   JOIN sessions s ON s.id=a.session_id
                   LEFT JOIN service_operations o ON o.session_id=a.session_id
                   WHERE a.kind='safety_evaluation'
                     AND a.created>=TIMESTAMPTZ 'epoch'+$1::bigint*interval '1 millisecond'
                     AND a.created<TIMESTAMPTZ 'epoch'+$2::bigint*interval '1 millisecond'
                     AND ($3::text IS NULL OR o.release_id=$3)
                     AND e.data->>'check' IS NOT NULL
                     AND e.data->>'phase' IN ('userInput','modelInput','modelOutput','toolCall','toolResult','event')
                     AND e.data->>'outcome' IN ('allow','blocked','redacted','matched','unavailable','alerted','would_block','shadow_unavailable')
                     AND (jsonb_typeof(s.data->'compliance') IS DISTINCT FROM 'object' OR
                         a.created>=now()-COALESCE((s.data->'compliance'->>'retentionDays')::bigint,3650)*interval '1 day')
                   UNION ALL
                   SELECT NULL, NULL, split_part(target,':',2), NULLIF(split_part(target,':',4),''), 'userInput', split_part(target,':',3), created
                   FROM api_security_audit
                   WHERE $3::text IS NULL AND action='turn.submit' AND session_id IS NULL
                     AND target ~ '^guardrail:[A-Za-z0-9._-]{1,128}:(allow|blocked|redacted|matched|unavailable|alerted|would_block|shadow_unavailable)(:[A-Za-z0-9._-]{1,128})?$'
                     AND created>=TIMESTAMPTZ 'epoch'+$1::bigint*interval '1 millisecond'
                     AND created<TIMESTAMPTZ 'epoch'+$2::bigint*interval '1 millisecond'
                 )
                 SELECT release_id,agent,policy,version,phase,count(*),
                        count(*) FILTER (WHERE outcome='unavailable') AS unavailable,
                        count(*) FILTER (WHERE outcome='shadow_unavailable') AS shadow_unavailable,
                        floor(extract(epoch FROM max(created))*1000)::bigint AS last_ms
                 FROM checks GROUP BY 1,2,3,4,5
                 ORDER BY unavailable DESC,shadow_unavailable DESC,last_ms DESC,release_id,agent,policy,version,phase LIMIT 101",
                &[&window.from, &window.until, &q.release],
            )?;
            let observed = rows.iter().take(100).map(|row| safe(json!({
                "release":row.get::<_, Option<String>>(0),
                "agent":row.get::<_, Option<String>>(1),
                "policy":row.get::<_, String>(2),
                "policy_version":row.get::<_, Option<String>>(3),
                "phase":row.get::<_, String>(4),
                "checks":row.get::<_, i64>(5),
                "unavailable":row.get::<_, i64>(6),
                "shadow_unavailable":row.get::<_, i64>(7),
                "last_ms":row.get::<_, i64>(8),
            }))).collect::<Vec<_>>();
            tx.commit()?;
            Ok(json!({"observed":observed,"truncated":rows.len()>100}))
        })
    }

    pub fn compliance_decisions(&self, q: Search) -> Result<Value> {
        let window = q.window()?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let identity = authorized(&mut tx, db.auth.as_ref(), Action::ComplianceRead)?;
            let access = Access::for_identity(&identity);
            let rows = decisions(&mut tx, &access, &q, &window, window.limit + 1)?;
            let mut response = page(rows, window.limit);
            response["apps"] = json!(visible_apps(&mut tx, &access)?);
            tx.commit()?;
            Ok(response)
        })
    }

    pub fn export_compliance_evidence(&self, q: Search, request: String) -> Result<Value> {
        let window = export_window(&q, &request)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let identity = authorized(&mut tx, db.auth.as_ref(), Action::EvidenceExport)?;
            let records = evidence(&mut tx, &identity, &q, &window)?;
            auth::audit_target(
                &mut tx,
                Some(&identity),
                Action::EvidenceExport,
                &request,
                q.app.as_deref().unwrap_or("scoped-decisions"),
            )?;
            tx.commit()?;
            Ok(json!({"records":records}))
        })
    }

    pub fn forward_compliance_evidence(
        &self,
        q: Search,
        request: String,
        sink: String,
        binding: String,
    ) -> Result<Value> {
        let window = export_window(&q, &request)?;
        ensure!(
            openworker_runtime::compliance::identifier(&sink) && binding.len() == 64,
            SubmissionError::InvalidRequest
        );
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let identity = authorized(&mut tx, db.auth.as_ref(), Action::EvidenceForward)?;
            let id = openworker_runtime::bundle::digest(&serde_json::to_vec(&(&identity.principal,&request))?);
            let selection = json!(openworker_runtime::bundle::digest(&serde_json::to_vec(&q)?));
            tx.query_one("SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('compliance-evidence-forward'))",&[])?;
            if let Some(row) = tx.query_opt("SELECT sink,binding,selection,record_count FROM compliance_evidence_deliveries WHERE id=$1",&[&id])? {
                ensure!(row.get::<_,String>(0)==sink && row.get::<_,String>(1)==binding && row.get::<_,Value>(2)==selection,SubmissionError::Conflict);
                return Ok(json!({"id":id,"records":row.get::<_,i64>(3)}));
            }
            tx.execute("DELETE FROM compliance_evidence_deliveries WHERE created < now()-INTERVAL '31 days' AND payload IS NULL",&[])?;
            let pending:i64 = tx.query_one("SELECT count(*) FROM compliance_evidence_deliveries WHERE payload IS NOT NULL",&[])?.get(0);
            ensure!(pending<128,SubmissionError::Busy);
            let records = evidence(&mut tx,&identity,&q,&window)?;
            let count = records.as_array().expect("evidence array").len() as i64;
            ensure!(count>0,SubmissionError::InvalidRequest);
            let payload = json!({"schemaVersion":1,"type":"openworker.compliance.evidence","deliveryId":id,"sink":sink,"records":records});
            let checkpoint = json!(Checkpoint::new(binding.clone()));
            tx.execute("INSERT INTO compliance_evidence_deliveries(id,actor,sink,binding,selection,record_count,payload,checkpoint)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8)", &[&id,&identity.principal,&sink,&binding,&selection,&count,&payload,&checkpoint])?;
            auth::audit_target(&mut tx,Some(&identity),Action::EvidenceForward,&request,q.app.as_deref().unwrap_or("scoped-decisions"))?;
            tx.commit()?;
            Ok(json!({"id":id,"records":count}))
        })
    }

    pub fn claim_evidence_delivery(
        &self,
        sink: String,
        binding: String,
        token: String,
    ) -> Result<Option<(String, Value)>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let now = openworker_runtime::store::now_ms();
            let row=tx.query_opt("SELECT id,checkpoint,payload FROM compliance_evidence_deliveries
                WHERE sink=$1 AND binding=$2 AND payload IS NOT NULL AND (checkpoint->>'nextAtMs')::bigint<=$3
                AND COALESCE((checkpoint->'lease'->>'untilMs')::bigint,0)<=$3
                ORDER BY created,id LIMIT 1 FOR UPDATE SKIP LOCKED",&[&sink,&binding,&(now as i64)])?;
            let Some(row)=row else { return Ok(None); };
            let id:String=row.get(0);
            let mut checkpoint:Checkpoint=serde_json::from_value(row.get(1))?;
            if !checkpoint.claim(&binding,&token,now,1)? { return Ok(None); }
            tx.execute("UPDATE compliance_evidence_deliveries SET checkpoint=$2 WHERE id=$1",&[&id,&json!(checkpoint)])?;
            tx.commit()?;
            Ok(Some((id,row.get(2))))
        })
    }

    pub fn settle_evidence_delivery(&self, id: String, token: String, success: bool) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        self.call(move |db| {
            let mut tx=db.client.transaction()?;
            let row=tx.query_one("SELECT checkpoint FROM compliance_evidence_deliveries WHERE id=$1 FOR UPDATE",&[&id])?;
            let mut checkpoint:Checkpoint=serde_json::from_value(row.get(0))?;
            checkpoint.settle(&token,openworker_runtime::store::now_ms(),success)?;
            tx.execute("UPDATE compliance_evidence_deliveries SET checkpoint=$2,payload=CASE WHEN $3 THEN NULL ELSE payload END WHERE id=$1",
                &[&id,&json!(checkpoint),&success])?;
            tx.commit()?;
            Ok(())
        })
    }

    pub fn compliance_evidence_deliveries(&self, targets: Vec<(String, String)>) -> Result<Value> {
        self.call(move |db| {
            let mut tx=db.client.transaction()?;
            authorized(&mut tx,db.auth.as_ref(),Action::EvidenceForward)?;
            let rows=tx.query("SELECT id,sink,binding,record_count,checkpoint,(extract(epoch FROM created)*1000)::bigint,actor
                FROM compliance_evidence_deliveries ORDER BY created DESC,id DESC LIMIT 50",&[])?;
            let mut deliveries = Vec::with_capacity(rows.len());
            for row in rows {
                let sink:String=row.get(1);
                let binding:String=row.get(2);
                let checkpoint:Checkpoint=serde_json::from_value(row.get(4))?;
                let status=if checkpoint.cursor>0 {"delivered"}
                    else if !targets.iter().any(|(name,pin)|name==&sink && pin==&binding) {"destination_unavailable"}
                    else if checkpoint.failures>0 {"retry_scheduled"}
                    else {"queued"};
                deliveries.push(safe(json!({"id":row.get::<_,String>(0),"sink":sink,"status":status,"records":row.get::<_,i64>(3),
                   "failures":checkpoint.failures,"next_ms":checkpoint.next_at_ms,"delivered_ms":checkpoint.last_success_ms,
                   "created_ms":row.get::<_,i64>(5),"actor":row.get::<_,String>(6)})));
            }
            tx.commit()?;
            Ok(json!({"deliveries":deliveries,"destinations":targets.into_iter().map(|(name,_)|name).collect::<Vec<_>>()}))
        })
    }

    pub fn compliance_agents(&self, q: Overview) -> Result<Value> {
        let search = Search {
            from_ms: q.from_ms,
            until_ms: q.until_ms,
            ..Search::default()
        };
        let window = search.window()?;
        if let Some(after) = &q.after {
            validate_id(after, 257)?;
        }
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let identity = authorized(&mut tx, db.auth.as_ref(), Action::ComplianceRead)?;
            let access = Access::for_identity(&identity);
            let term = None;
            let mut args = access.params(&search, &window, &term).to_vec();
            args.push(&q.after);
            let rows = tx.query(&format!("{DECISIONS}{AGENT_OVERVIEW}"), &args)?;
            let next = (rows.len() > 50).then(|| rows[49].get::<_, String>(0));
            let agents = rows
                .iter()
                .take(50)
                .map(|row| safe(row.get(1)))
                .collect::<Vec<_>>();
            let apps = visible_apps(&mut tx, &access)?;
            tx.commit()?;
            Ok(json!({"agents":agents,"apps":apps,"next":next}))
        })
    }

    pub fn compliance_admin_audit(&self, q: AuditSearch) -> Result<Value> {
        let window = Search {
            app: q.app.clone(),
            q: q.q.clone(),
            from_ms: q.from_ms,
            until_ms: q.until_ms,
            limit: q.limit,
            ..Search::default()
        }
        .window()?;
        ensure!(
            q.before.is_none_or(|id| id > 0),
            SubmissionError::InvalidRequest
        );
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let identity = authorized(&mut tx, db.auth.as_ref(), Action::ComplianceRead)?;
            let access = Access::for_identity(&identity);
            let term =
                q.q.as_ref()
                    .map(|s| s.trim().to_lowercase())
                    .filter(|s| !s.is_empty());
            let rows = tx.query(
                ADMIN_AUDIT,
                &[
                    &access.all,
                    &access.apps,
                    &access.sessions,
                    &window.from,
                    &window.until,
                    &identity.principal,
                    &q.app,
                    &term,
                    &q.before,
                    &(window.limit + 1),
                ],
            )?;
            let next = (rows.len() > window.limit as usize)
                .then(|| rows[window.limit as usize - 1].get::<_, i64>(0));
            let records = rows
                .iter()
                .take(window.limit as usize)
                .map(|row| safe(row.get(1)))
                .collect::<Vec<_>>();
            let apps = visible_apps(&mut tx, &access)?;
            tx.commit()?;
            Ok(json!({"records":records,"next":next,"apps":apps}))
        })
    }
}
