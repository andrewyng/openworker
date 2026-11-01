//! Trusted publication outbox. Original actor -> lead -> team -> outbox lock
//! order matches model mutations/cancellation. Lost dispatch is inspection only.
use crate::{agent_team::Tx, auth, db, PostgresStore};
use anyhow::{ensure, Context, Result};
use openworker_artifacts::Artifact;
use openworker_runtime::{
    agent_team::{Document, Message, Transaction},
    coding_handoff::publication::{self, Outcome, Publication, Request},
    store::Session,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Intent {
    pub request: Request,
    pub lead: String,
    pub principal: String,
    pub release_id: String,
    pub release_digest: String,
    pub profile_id: String,
    pub profile_digest: String,
    pub publication: Publication,
    pub artifact: Artifact,
}
#[derive(Clone, Debug)]
pub struct Job {
    pub intent: Intent,
    pub claim: String,
    pub dispatch: bool,
}
pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_team_publications(id TEXT PRIMARY KEY,team TEXT NOT NULL REFERENCES agent_teams(id),data JSONB NOT NULL,status TEXT NOT NULL DEFAULT 'pending',dispatched BOOLEAN NOT NULL DEFAULT FALSE,claim TEXT,next_check TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),last_notice TEXT)")?;
    Ok(())
}
fn members(
    tx: &mut postgres::Transaction<'_>,
    team: &str,
    actor_id: &str,
) -> Result<(Session, Session, Document)> {
    let actor = db::locked(tx, actor_id, None)?;
    let (lead, d) = crate::service_team_submissions::document(tx, team)?;
    ensure!(
        actor.agent_team.as_ref().is_some_and(|m| m.team_id == d.id
            && d.roster
                .instance(&m.agent_id)
                .is_some_and(|i| i.session_id == actor.id)),
        "Original publishing instance changed"
    );
    Ok((actor, lead, d))
}
fn build(
    tx: &mut postgres::Transaction<'_>,
    actor: &Session,
    lead: &Session,
    d: &Document,
    id: &str,
    inspect_only: bool,
) -> Result<Intent> {
    ensure!(
        d.coding_selection && !d.stopped,
        "Original coding team unavailable"
    );
    let (identity, _) = crate::agent_team::lead_authority(tx, lead, d)?;
    let state = d
        .publications
        .get(id)
        .context("Original publication removed")?;
    ensure!(
        state.error.is_none() && matches!(state.outcome, None | Some(Outcome::Unknown { .. })),
        "Publication already settled or blocked"
    );
    let request = &state.request;
    ensure!(
        request.actor.session_id == actor.id
            && request.actor.team_id == d.id
            && actor
                .agent_team
                .as_ref()
                .is_some_and(|m| m.agent_id == request.actor.agent_id),
        "Original publisher changed"
    );
    let member = d
        .roster
        .instance(&request.actor.agent_id)
        .context("Original publisher absent")?;
    if !inspect_only {
        ensure!(
            openworker_runtime::team_lifecycle::accepted_effect(
                d,
                &member.agent_id,
                &request.origin.turn_id
            ) && (!actor.closed || d.retirements.contains_key(&member.agent_id)),
            "Publisher no longer admits new effects"
        );
        ensure!(
            !actor.turn.as_ref().is_some_and(
                |t| t.id == request.origin.turn_id && matches!(t.status.as_str(), "cancelled")
            ),
            "Originating publication turn cancelled or interrupted"
        );
        let status: Option<String> = tx
            .query_opt(
                "SELECT status FROM submissions WHERE session_id=$1 AND turn_id=$2",
                &[&actor.id, &request.origin.turn_id],
            )?
            .map(|r| r.get(0));
        ensure!(
            !status.as_deref().is_some_and(|s| matches!(s, "cancelled")),
            "Originating publication turn cancelled or interrupted"
        );
    }
    let scope = publication::access(d, actor).context("Original repository access unavailable")?;
    ensure!(
        scope.grant == request.grant && scope.digest == request.grant_digest,
        "Original repository grant changed"
    );
    let profile_id = if actor.id == lead.id {
        scope_profile(d)?
    } else if let Some(binding) = d.placement_bindings.get(&request.actor.agent_id) {
        binding.profile_id.clone()
    } else {
        scope_profile(d)?
    };
    let entry = crate::service_catalog::entry(tx, "profile", &profile_id)?;
    let profile: crate::service_catalog::Profile = serde_json::from_value(entry.definition)?;
    ensure!(
        entry.activated && profile.repository_access.as_deref() == Some(&request.grant),
        "Original repository profile unavailable"
    );
    crate::service_catalog::allowed(&identity, &profile.allowed_principals)?;
    if actor.id != lead.id {
        crate::agent_team::workspace_authority(tx, lead, d, &request.actor.agent_id)?;
    }
    let (manifest, bundle, artifact) =
        crate::service_team_submissions::ready_snapshot(tx, lead, d, &request.submission_id)?;
    request.require_manifest(&manifest)?;
    let repo = scope
        .repositories
        .get(&manifest.repository)
        .context("Original repository scope denied")?;
    ensure!(
        repo.write_prefixes
            .iter()
            .any(|p| request.branch.starts_with(p) && request.branch.len() > p.len()),
        "Original branch scope denied"
    );
    let release_id: String = tx
        .query_one(
            "SELECT release_id FROM service_operations WHERE session_id=$1 AND status='complete'",
            &[&lead.id],
        )?
        .get(0);
    let release = crate::service_catalog::entry(tx, "release", &release_id)?;
    let publication = Publication {
        id: request.id.clone(),
        grant_digest: request.grant_digest.clone(),
        bundle,
        branch: request.branch.clone(),
        expected_head: request.expected_head.clone(),
    };
    publication.validate()?;
    Ok(Intent {
        request: request.clone(),
        lead: lead.id.clone(),
        principal: identity.principal,
        release_id,
        release_digest: release.digest,
        profile_id,
        profile_digest: entry.digest,
        publication,
        artifact,
    })
}
fn scope_profile(d: &Document) -> Result<String> {
    Ok(d.repository_access
        .as_ref()
        .context("Missing original repository catalog")?
        .lead_profile
        .clone())
}
fn original(tx: &mut postgres::Transaction<'_>, job: &Job) -> Result<(Session, Session, Document)> {
    let (actor, lead, d) = members(
        tx,
        &job.intent.request.actor.team_id,
        &job.intent.request.actor.session_id,
    )?;
    let row=tx.query_one("SELECT data,claim,next_check>clock_timestamp(),status FROM service_team_publications WHERE id=$1 FOR UPDATE",&[&job.intent.request.id])?;
    let intent: Intent = serde_json::from_value(row.get(0))?;
    ensure!(
        intent == job.intent
            && row.get::<_, Option<String>>(1).as_deref() == Some(&job.claim)
            && row.get::<_, bool>(2)
            && row.get::<_, String>(3) == "publishing",
        "Stale publication claim"
    );
    Ok((actor, lead, d))
}
fn notice(
    tx: &mut postgres::Transaction<'_>,
    d: &mut Document,
    intent: &Intent,
    phase: &str,
) -> Result<()> {
    let old: Option<String> = tx
        .query_one(
            "SELECT last_notice FROM service_team_publications WHERE id=$1",
            &[&intent.request.id],
        )?
        .get(0);
    if old.as_deref() == Some(phase) {
        return Ok(());
    }
    d.sequence = d
        .sequence
        .checked_add(1)
        .filter(|s| *s <= i64::MAX as u64)
        .context("Team inbox sequence exhausted")?;
    let recipients =
        std::collections::BTreeSet::from([d.lead.clone(), intent.request.actor.agent_id.clone()])
            .into_iter()
            .collect();
    Tx(tx).append(&d.id,&Message{id:openworker_runtime::id("tm"),sequence:d.sequence,sender:"harness".into(),recipients,board:false,body:format!("Harness repository publication status (not a permission grant). Original operation {}: {}. Inspect the original branch before choosing a new action; an uncertain push is never automatically repeated.",intent.request.id,phase)})?;
    tx.execute(
        "UPDATE service_team_publications SET last_notice=$2 WHERE id=$1",
        &[&intent.request.id, &phase],
    )?;
    Ok(())
}
impl PostgresStore {
    pub fn claim_team_publication(&self, installation: &str) -> Result<Option<Job>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let installation = installation.to_owned();
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            let c=tx.query_opt("SELECT t.id,p.key,p.value->'request'->'actor'->>'session_id' FROM agent_teams t JOIN sessions l ON l.id=t.data->'roster'->'instances'->(t.data->>'lead')->>'session_id' JOIN service_operations o ON o.session_id=l.id JOIN service_catalog c ON c.kind='release' AND c.id=o.release_id CROSS JOIN LATERAL jsonb_each(coalesce(t.data->'publications','{}'::jsonb)) p LEFT JOIN service_team_publications q ON q.id=p.key WHERE c.data->>'installation'=$1 AND (p.value->'outcome'='null'::jsonb OR p.value->'outcome'->>'status'='unknown') AND coalesce(p.value->'error','null'::jsonb)='null'::jsonb AND (q.id IS NULL OR (q.status IN ('pending','publishing','unknown') AND q.next_check<=clock_timestamp())) ORDER BY t.id,p.key LIMIT 1",&[&installation])?;
            let Some(c)=c else{tx.commit()?;return Ok(None)};
            let team:String=c.get(0);let id:String=c.get(1);let (actor,lead,mut d)=members(&mut tx,&team,&c.get::<_,String>(2))?;
            let row=tx.query_opt("SELECT data,dispatched,next_check<=clock_timestamp(),status FROM service_team_publications WHERE id=$1 FOR UPDATE",&[&id])?;
            if row.as_ref().is_some_and(|r|!r.get::<_,bool>(2) || !["pending","publishing","unknown"].contains(&r.get::<_,String>(3).as_str())){tx.commit()?;return Ok(None)}
            let dispatch=row.as_ref().is_none_or(|r|!r.get::<_,bool>(1));
            if dispatch && crate::service_admin::current(&mut tx)?.configuration.admissions_paused {tx.commit()?;return Ok(None)}
            let prepared=build(&mut tx,&actor,&lead,&d,&id,!dispatch);
            let intent=match prepared{Ok(i)=>i,Err(e) if e.downcast_ref::<postgres::Error>().is_some()=>return Err(e),Err(_)=>{
                d.publications.get_mut(&id).context("Original publication missing")?.error=Some("Original publication authority unavailable; inspect original work".into());
                if row.is_some(){tx.execute("UPDATE service_team_publications SET status='blocked',claim=NULL WHERE id=$1",&[&id])?;}
                d.sequence=d.sequence.checked_add(1).filter(|n|*n<=i64::MAX as u64).context("Inbox sequence exhausted")?;
                let publisher=d.publications[&id].request.actor.agent_id.clone();
                Tx(&mut tx).append(&d.id,&Message{id:openworker_runtime::id("tm"),sequence:d.sequence,sender:"harness".into(),recipients:std::collections::BTreeSet::from([d.lead.clone(),publisher]).into_iter().collect(),board:false,body:format!("Harness publication status (not a permission grant). Original operation {id} is blocked because its authority is unavailable. Original work is retained; no new dispatch was authorized.")})?;
                Tx(&mut tx).write(&d,false)?;crate::team_execution::session_event(&mut tx,&lead.id,None,"team_publication",json!({"publication_id":id,"phase":"blocked","dispatch_consumed":!dispatch}))?;tx.commit()?;return Ok(None)
            }};
            if let Some(row)=row {ensure!(serde_json::from_value::<Intent>(row.get(0))?==intent,"Original publication scope changed");}
            else {tx.execute("INSERT INTO service_team_publications(id,team,data) VALUES($1,$2,$3)",&[&id,&team,&json!(intent)])?;}
            let claim=uuid::Uuid::new_v4().to_string();
            tx.execute("UPDATE service_team_publications SET dispatched=TRUE,status='publishing',claim=$2,next_check=clock_timestamp()+interval '180 seconds' WHERE id=$1",&[&id,&claim])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_publication",json!({"publication_id":id,"submission_id":intent.request.submission_id,"phase":"publishing","dispatch":dispatch}))?;
            tx.commit()?;Ok(Some(Job{intent,claim,dispatch}))
        })
    }
    pub fn check_team_publication(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let (actor, lead, d) = original(&mut tx, &job)?;
            ensure!(
                build(
                    &mut tx,
                    &actor,
                    &lead,
                    &d,
                    &job.intent.request.id,
                    !job.dispatch
                )? == job.intent,
                "Original publication changed"
            );
            tx.commit()?;
            Ok(())
        })
    }
    pub fn complete_team_publication(&self, job: &Job, outcome: Outcome) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        outcome.require_request(&job.intent.publication)?;
        let job = job.clone();
        self.call(move|db|{let mut tx=db.client.transaction()?;let (actor,lead,mut d)=original(&mut tx,&job)?;ensure!(build(&mut tx,&actor,&lead,&d,&job.intent.request.id,!job.dispatch)?==job.intent,"Original publication changed");
            let phase=match &outcome{Outcome::Published{..}=>"published",Outcome::Rejected{..}=>"rejected",Outcome::Unknown{..}=>"unknown"};
            let state=d.publications.get_mut(&job.intent.request.id).context("Original publication missing")?;state.outcome=Some(outcome);state.uncertain=phase=="unknown";
            notice(&mut tx,&mut d,&job.intent,phase)?;Tx(&mut tx).write(&d,false)?;
            tx.execute("UPDATE service_team_publications SET status=$2,claim=NULL,next_check=clock_timestamp()+interval '30 seconds' WHERE id=$1",&[&job.intent.request.id,&phase])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_publication",json!({"publication_id":job.intent.request.id,"phase":phase,"inspection_only":!job.dispatch}))?;tx.commit()?;Ok(())})
    }
    pub fn defer_team_publication(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move|db|{let mut tx=db.client.transaction()?;let (actor,lead,mut d)=original(&mut tx,&job)?;
            let blocked=match build(&mut tx,&actor,&lead,&d,&job.intent.request.id,true){Ok(i)=>i!=job.intent,Err(e) if e.downcast_ref::<postgres::Error>().is_some()=>return Err(e),Err(_)=>true};
            let phase=if blocked{"blocked"}else{"unknown"};let state=d.publications.get_mut(&job.intent.request.id).context("Original publication missing")?;state.uncertain=true;if blocked{state.error=Some("Original publication authority unavailable; outcome may be unknown".into());}
            notice(&mut tx,&mut d,&job.intent,phase)?;Tx(&mut tx).write(&d,false)?;
            tx.execute("UPDATE service_team_publications SET status=$2,claim=NULL,next_check=clock_timestamp()+interval '30 seconds' WHERE id=$1",&[&job.intent.request.id,&phase])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_publication",json!({"publication_id":job.intent.request.id,"phase":"outcome_unknown","blocked":blocked,"inspection_only":true}))?;tx.commit()?;Ok(())})
    }
}
impl PostgresStore {
    /// Only the executing worker's registered native callback can enqueue this
    /// effect. Ordinary REST/team commands cannot supply a native ToolContext.
    pub fn queue_registered_publication(
        &self,
        sid: &str,
        turn: &str,
        call: &str,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (sid, turn, call) = (sid.to_owned(), turn.to_owned(), call.to_owned());
        self.call(move |db| {
            let epoch = db.epoch(&sid)?;
            let mut tx = db.client.transaction()?;
            let session = db::locked(&mut tx, &sid, Some((&db.owner, epoch)))?;
            let current = session.turn.as_ref().context("No executing native turn")?;
            let first = current.queue.first().context("No executing native call")?;
            ensure!(
                !session.closed
                    && current.id == turn
                    && current.status == "executing"
                    && first["call_id"] == call
                    && first["name"] == publication::ALIAS,
                "Stale or unrelated native tool callback"
            );
            let expected: serde_json::Value = serde_json::from_str(
                first["arguments"]
                    .as_str()
                    .context("Missing native call arguments")?,
            )?;
            ensure!(expected == arguments, "Native callback arguments changed");
            openworker_runtime::coding_handoff::workflow::require_tool_approval(
                &session,
                first,
                Some("github_broker"),
                Some(publication::TOOL),
            )?;
            #[derive(Deserialize)]
            #[serde(deny_unknown_fields)]
            struct Args {
                submission_id: String,
                branch: String,
                expected_head: Option<String>,
            }
            let a: Args = serde_json::from_value(arguments)?;
            let member = session.agent_team.as_ref().context("Not a team member")?;
            let mut d = Tx(&mut tx).load(&member.team_id)?;
            ensure!(
                d.roster
                    .instance(&member.agent_id)
                    .is_some_and(|i| i.session_id == session.id),
                "Original native instance changed"
            );
            let result = publication::request(
                &mut d,
                &session,
                a.submission_id,
                a.branch,
                a.expected_head,
                openworker_runtime::coding_handoff::workflow::Call {
                    turn_id: turn.clone(),
                    call_id: call,
                },
            )?;
            Tx(&mut tx).write(&d, false)?;
            crate::team_execution::session_event(
                &mut tx,
                &sid,
                Some(&turn),
                "team_publication_requested",
                serde_json::json!({"publication_id":result["id"],"phase":result["status"]}),
            )?;
            tx.commit()?;
            Ok(result)
        })
    }
}
