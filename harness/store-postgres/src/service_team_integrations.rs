//! Explicit lead integration outbox. Unknown mutation outcome is observed on the
//! ORIGINAL resource; the control plane never retries read-tree or Git ref writes.
use crate::{agent_team::Tx, auth, db, PostgresStore};
use anyhow::{ensure, Context, Result};
use openworker_runtime::{
    agent_team::{Document, Message, Transaction},
    coding_handoff::{
        git::Export,
        integrate::{Observation, Request},
    },
    environment,
    store::Session,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Intent {
    pub id: String,
    pub team: String,
    pub lead: String,
    pub environment: environment::Binding,
    pub root: String,
    pub profile_id: String,
    pub profile_digest: String,
    pub release_id: String,
    pub release_digest: String,
    pub request: Request,
}
#[derive(Clone, Debug)]
pub struct Job {
    pub intent: Intent,
    pub claim: String,
    pub dispatch: bool,
}
pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_team_integrations(id TEXT PRIMARY KEY,team TEXT NOT NULL REFERENCES agent_teams(id),data JSONB NOT NULL,status TEXT NOT NULL DEFAULT 'pending',dispatched BOOLEAN NOT NULL DEFAULT FALSE,claim TEXT,next_check TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),last_notice TEXT);")?;
    Ok(())
}
fn build(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
    id: &str,
) -> Result<Intent> {
    ensure!(
        d.coding_selection
            && !d.stopped
            && d.roster
                .instance(&d.lead)
                .is_some_and(|i| i.session_id == lead.id),
        "Original coding lead unavailable"
    );
    let (_, profile) = crate::agent_team::lead_authority(tx, lead, d)?;
    let p = crate::service_catalog::entry(tx, "profile", &profile.id)?;
    let r = tx.query_one(
        "SELECT release_id FROM service_operations WHERE session_id=$1 AND status='complete'",
        &[&lead.id],
    )?;
    let release = crate::service_catalog::entry(tx, "release", &r.get::<_, String>(0))?;
    let state = d
        .integrations
        .get(id)
        .context("Unknown original integration")?;
    ensure!(state.outcome.is_none(), "Integration already acknowledged");
    let source = d.coding.as_ref().context("Missing original source")?;
    let (initial, _, _) = crate::service_team_sources::ready_snapshot(tx, lead, d)?;
    let (manifest, _, _) =
        crate::service_team_submissions::ready_snapshot(tx, lead, d, &state.request.submission_id)?;
    state.request.require_submission(
        &d.submissions[&state.request.submission_id].request,
        &manifest,
    )?;
    ensure!(
        initial.repository == manifest.repository && initial.base_commit == manifest.base_commit,
        "Submission differs from original source"
    );
    let reception = d
        .receptions
        .get(&state.request.submission_id)
        .context("Missing original reception")?;
    ensure!(
        reception.manifest_digest == manifest.digest()?,
        "Original received manifest changed"
    );
    let received = reception
        .receipt
        .clone()
        .context("Original reception not acknowledged")?;
    received.validate_scope(std::path::Path::new(&lead.workspace), &received.path)?;
    let environment = lead
        .environment
        .clone()
        .context("Missing lead environment")?;
    let env: environment::Record = serde_json::from_value(
        tx.query_one(
            "SELECT data FROM environments WHERE id=$1 FOR SHARE",
            &[&environment.environment_id],
        )?
        .get(0),
    )?;
    ensure!(
        env.phase == environment::Phase::Ready && env.binding.as_ref() == Some(&environment),
        "Original integration environment unavailable"
    );
    let scope_digest = openworker_runtime::bundle::digest(&serde_json::to_vec(&(
        "coding-integration-io-v1",
        id,
        &d.id,
        &lead.id,
        &lead.workspace,
        &environment,
        &source.source,
        &state.request,
        &received,
        &p.digest,
        &release.digest,
    ))?);
    let request = Request {
        export: Export {
            operation_id: id.into(),
            scope_digest,
            repository: source.source.repository.clone(),
            directory: source.source.directory.clone(),
            object_format: manifest.object_format,
            base_commit: manifest.base_commit,
            submitted_commit: manifest.submitted_commit,
        },
        expected_head: state.request.expected_head.clone(),
        received,
    };
    request.validate()?;
    Ok(Intent {
        id: id.into(),
        team: d.id.clone(),
        lead: lead.id.clone(),
        environment,
        root: lead.workspace.clone(),
        profile_id: p.id,
        profile_digest: p.digest,
        release_id: release.id,
        release_digest: release.digest,
        request,
    })
}
fn original(tx: &mut postgres::Transaction<'_>, job: &Job) -> Result<(Session, Document)> {
    let lead = db::locked(tx, &job.intent.lead, None)?;
    let d = Tx(tx).load(&job.intent.team)?;
    let row=tx.query_one("SELECT data,claim,next_check>clock_timestamp(),status FROM service_team_integrations WHERE id=$1 FOR UPDATE",&[&job.intent.id])?;
    let i: Intent = serde_json::from_value(row.get(0))?;
    ensure!(
        i == job.intent
            && row.get::<_, Option<String>>(1).as_deref() == Some(&job.claim)
            && row.get::<_, bool>(2)
            && matches!(row.get::<_, String>(3).as_str(), "dispatching" | "unknown"),
        "Stale integration claim"
    );
    Ok((lead, d))
}
fn notice(tx: &mut postgres::Transaction<'_>, d: &Document, i: &Intent, key: &str) -> Result<()> {
    let old: Option<String> = tx
        .query_one(
            "SELECT last_notice FROM service_team_integrations WHERE id=$1",
            &[&i.id],
        )?
        .get(0);
    if old.as_deref() == Some(key) {
        return Ok(());
    }
    let mut d = d.clone();
    d.sequence = d
        .sequence
        .checked_add(1)
        .filter(|n| *n <= i64::MAX as u64)
        .context("Inbox sequence exhausted")?;
    let message=Message{id:openworker_runtime::id("tm"),sequence:d.sequence,sender:"harness".into(),recipients:vec![d.lead.clone()],board:false,body:format!("Harness integration status (not verification or permission). Operation {}: {}. Read the submission catalog for the original outcome. Unknown work is retained and is never automatically repeated.",i.id,key)};
    Tx(tx).append(&d.id, &message)?;
    Tx(tx).write(&d, false)?;
    tx.execute(
        "UPDATE service_team_integrations SET last_notice=$2 WHERE id=$1",
        &[&i.id, &key],
    )?;
    Ok(())
}
impl PostgresStore {
    pub fn claim_team_integration(&self, installation: &str) -> Result<Option<Job>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let installation = installation.to_owned();
        self.call(move|db| {
            let mut tx=db.client.transaction()?;
            let c=tx.query_opt("SELECT l.id,t.id,s.key FROM agent_teams t JOIN sessions l ON l.id=t.data->'roster'->'instances'->(t.data->>'lead')->>'session_id' JOIN service_operations o ON o.session_id=l.id JOIN service_catalog c ON c.kind='release' AND c.id=o.release_id CROSS JOIN LATERAL jsonb_each(coalesce(t.data->'integrations','{}'::jsonb)) s LEFT JOIN service_team_integrations q ON q.id=s.key WHERE c.data->>'installation'=$1 AND NOT coalesce((t.data->>'stopped')::boolean,false) AND s.value->'outcome'='null'::jsonb AND s.value->'error'='null'::jsonb AND (q.id IS NULL OR (q.status IN ('pending','dispatching','unknown') AND q.next_check<=clock_timestamp())) ORDER BY t.id,s.key LIMIT 1 FOR UPDATE OF l SKIP LOCKED",&[&installation])?;
            let Some(c)=c else {tx.commit()?;return Ok(None)};
            let lead=db::locked(&mut tx,&c.get::<_,String>(0),None)?;let team:String=c.get(1);let id:String=c.get(2);let mut d=Tx(&mut tx).load(&team)?;
            let state=d.integrations.get(&id).context("Missing original integration")?;
            if state.outcome.is_some() || state.error.is_some(){tx.commit()?;return Ok(None)}
            if crate::service_admin::current(&mut tx)?.configuration.admissions_paused {tx.commit()?;return Ok(None)}
            let row=tx.query_opt("SELECT data,dispatched,next_check<=clock_timestamp(),status FROM service_team_integrations WHERE id=$1 FOR UPDATE",&[&id])?;
            if row.as_ref().is_some_and(|r|!r.get::<_,bool>(2)||!["pending","dispatching","unknown"].contains(&r.get::<_,String>(3).as_str())){tx.commit()?;return Ok(None)}
            let dispatch=row.as_ref().is_none_or(|r|!r.get::<_,bool>(1));
            let prepared=build(&mut tx,&lead,&d,&id);
            let i=match prepared {Ok(i)=>i,Err(e) if e.downcast_ref::<postgres::Error>().is_some()=>return Err(e),Err(_)=>{
                d.integrations.get_mut(&id).context("Missing original integration")?.error=Some("Original integration authority unavailable; original work retained".into());Tx(&mut tx).write(&d,false)?;
                if row.is_some(){tx.execute("UPDATE service_team_integrations SET status='blocked',claim=NULL WHERE id=$1",&[&id])?;}
                crate::team_execution::session_event(&mut tx,&lead.id,None,"team_integration",json!({"integration_id":id,"phase":"blocked","dispatch_consumed":!dispatch}))?;tx.commit()?;return Ok(None)
            }};
            if let Some(row)=row {let saved:Intent=serde_json::from_value(row.get(0))?;ensure!(saved==i,"Original integration scope changed");}
            else {tx.execute("INSERT INTO service_team_integrations(id,team,data) VALUES($1,$2,$3)",&[&id,&team,&json!(i)])?;}
            let claim=uuid::Uuid::new_v4().to_string();tx.execute("UPDATE service_team_integrations SET dispatched=TRUE,status='dispatching',claim=$2,next_check=clock_timestamp()+interval '180 seconds' WHERE id=$1",&[&id,&claim])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_integration",json!({"integration_id":id,"submission_id":d.integrations[&id].request.submission_id,"phase":"dispatching","dispatch":dispatch}))?;tx.commit()?;Ok(Some(Job{intent:i,claim,dispatch}))
        })
    }
    pub fn check_team_integration(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let (lead, d) = original(&mut tx, &job)?;
            ensure!(
                build(&mut tx, &lead, &d, &job.intent.id)? == job.intent,
                "Original integration scope changed"
            );
            tx.commit()?;
            Ok(())
        })
    }
    pub fn complete_team_integration(&self, job: &Job, outcome: Observation) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        outcome.require_request(&job.intent.request)?;
        let job = job.clone();
        self.call(move|db|{let mut tx=db.client.transaction()?;let (lead,mut d)=original(&mut tx,&job)?;ensure!(build(&mut tx,&lead,&d,&job.intent.id)?==job.intent,"Original integration scope changed");
            let phase=match outcome{Observation::Applied{..}=>"applied",Observation::Rejected{..}=>"rejected"};
            d.integrations.get_mut(&job.intent.id).context("Missing original integration")?.outcome=Some(outcome);d.integrations.get_mut(&job.intent.id).unwrap().uncertain=false;Tx(&mut tx).write(&d,false)?;
            tx.execute("UPDATE service_team_integrations SET status=$2,claim=NULL WHERE id=$1",&[&job.intent.id,&phase])?;
            notice(&mut tx,&d,&job.intent,phase)?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_integration",json!({"integration_id":job.intent.id,"phase":phase,"inspection_only":!job.dispatch}))?;tx.commit()?;Ok(())})
    }
    pub fn defer_team_integration(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move|db|{let mut tx=db.client.transaction()?;let (lead,mut d)=original(&mut tx,&job)?;
            let blocked=match build(&mut tx,&lead,&d,&job.intent.id){Ok(i)=>i!=job.intent,Err(e) if e.downcast_ref::<postgres::Error>().is_some()=>return Err(e),Err(_)=>true};
            let phase=if blocked{"blocked"}else{"unknown"};
            d.integrations.get_mut(&job.intent.id).context("Missing original integration")?.uncertain=true;
            Tx(&mut tx).write(&d,false)?;
            if blocked {d.integrations.get_mut(&job.intent.id).context("Missing original integration")?.error=Some("Original integration authority unavailable; original work retained".into());Tx(&mut tx).write(&d,false)?;}
            tx.execute("UPDATE service_team_integrations SET status=$2,claim=NULL,next_check=clock_timestamp()+interval '30 seconds' WHERE id=$1",&[&job.intent.id,&phase])?;
            notice(&mut tx,&d,&job.intent,phase)?;crate::team_execution::session_event(&mut tx,&lead.id,None,"team_integration",json!({"integration_id":job.intent.id,"phase":"outcome_unknown","blocked":blocked,"inspection_only":true}))?;tx.commit()?;Ok(())})
    }
}
