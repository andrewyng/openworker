//! Private child-source outbox. Intent and first-dispatch bits commit before I/O.
//! Transport loss permits inspection of the original operation, never a second
//! checkout, replacement destination or fallback to an unrelated source.
use crate::{agent_team::Tx, auth, db, PostgresStore};
use anyhow::{ensure, Context, Result};
use openworker_artifacts::Artifact;
use openworker_runtime::{
    agent_team::{self, Document, Message, Transaction},
    coding_handoff::{config::Choice, transfer::Transfer, Import},
    environment::{self, Phase},
    store::Session,
    team_coordination,
    team_workspace::{Receipt, Request, Selection},
};
use serde::{Deserialize, Serialize};
use serde_json::json;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Intent {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reception: Option<String>,
    pub team: String,
    pub agent: String,
    pub lead: String,
    pub choice: Choice,
    pub environment: environment::Binding,
    pub root: String,
    pub profile_id: String,
    pub profile_digest: String,
    pub workspace: Request,
    pub transfer: Transfer,
    pub artifact: Artifact,
}
#[derive(Clone, Debug)]
pub struct Job {
    pub intent: Intent,
    pub claim: String,
    pub prepare: bool,
    pub checkout_dispatched: bool,
}
pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_team_imports(team TEXT NOT NULL REFERENCES agent_teams(id),agent TEXT NOT NULL,data JSONB NOT NULL,status TEXT NOT NULL DEFAULT 'pending',prepare_dispatched BOOLEAN NOT NULL DEFAULT FALSE,checkout_dispatched BOOLEAN NOT NULL DEFAULT FALSE,claim TEXT,next_check TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),last_notice TEXT,PRIMARY KEY(team,agent));")?;
    Ok(())
}
pub(crate) fn placement(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
    agent: &str,
) -> Result<(environment::Binding, String, String, String)> {
    crate::agent_team::workspace_authority(tx, lead, d, agent)?;
    let (root, _) = openworker_runtime::team_placement::workspace_scope(d, lead, agent)?;
    let (environment, profile) = if let Some(b) = d.placement_bindings.get(agent) {
        (b.environment.clone(), b.profile_id.clone())
    } else {
        ensure!(
            d.sandbox_choices
                .get(agent)
                .is_none_or(|c| *c == Default::default()),
            "Selected child placement is not ready"
        );
        (lead.environment.clone().context("Lead environment unavailable")?,
            tx.query_one("SELECT profile_id FROM service_operations WHERE session_id=$1 AND status='complete'", &[&lead.id])?.get(0))
    };
    let p = crate::service_team_placements::target_authority(tx, lead, d, agent, &profile)?;
    if let Some(b) = d.placement_bindings.get(agent) {
        ensure!(
            p.digest == b.profile_digest,
            "Original target profile changed"
        );
    }
    let e: environment::Record = serde_json::from_value(
        tx.query_one(
            "SELECT data FROM environments WHERE id=$1 FOR SHARE",
            &[&environment.environment_id],
        )?
        .get(0),
    )?;
    ensure!(
        e.phase == Phase::Ready && e.binding.as_ref() == Some(&environment),
        "Original child environment unavailable or replaced"
    );
    Ok((environment, root, p.id, p.digest))
}
fn authority(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
    i: &Intent,
) -> Result<()> {
    if i.reception.is_some() {
        return crate::service_team_receptions::authority(tx, lead, d, i);
    }
    ensure!(
        !d.stopped
            && d.coding_selection
            && d.roster
                .instance(&d.lead)
                .is_some_and(|a| a.session_id == lead.id)
            && d.roster
                .instance(&i.agent)
                .is_some_and(|a| a.phase == team_coordination::Phase::Reserved)
            && d.id == i.team
            && lead.id == i.lead
            && d.roster.request(&i.agent).and_then(|r| r.source.as_ref()) == Some(&i.choice),
        "Original source reservation unavailable"
    );
    let (env, root, profile, digest) = placement(tx, lead, d, &i.agent)?;
    ensure!(
        env == i.environment
            && root == i.root
            && profile == i.profile_id
            && digest == i.profile_digest,
        "Original import placement changed"
    );
    let (manifest, bundle, artifact) = match i.choice {
        Choice::Initial => crate::service_team_sources::ready_snapshot(tx, lead, d)?,
        Choice::Submission { ref id } => {
            crate::service_team_submissions::ready_snapshot(tx, lead, d, id)?
        }
    };
    ensure!(
        manifest == i.transfer.manifest
            && bundle == i.transfer.bundle
            && artifact == i.artifact
            && d.workspaces
                .get(&i.agent)
                .is_some_and(|w| w.request == i.workspace && w.error.is_none()),
        "Original source/import receipt changed"
    );
    i.transfer.validate()?;
    ensure!(
        i.transfer.request.operation_id == i.workspace.operation_id
            && i.transfer.request.scope_digest == i.workspace.scope_digest
            && i.workspace.selection
                == (Selection::Directory {
                    path: i.transfer.request.directory.clone()
                }),
        "Import workspace binding changed"
    );
    Ok(())
}
fn original(tx: &mut postgres::Transaction<'_>, job: &Job) -> Result<(Session, Document)> {
    let lead = db::locked(tx, &job.intent.lead, None)?;
    let d = Tx(tx).load(&job.intent.team)?;
    let row = tx.query_one("SELECT data,claim,next_check>clock_timestamp(),checkout_dispatched,status FROM service_team_imports WHERE team=$1 AND agent=$2 FOR UPDATE", &[&job.intent.team, &job.intent.agent])?;
    let i: Intent = serde_json::from_value(row.get(0))?;
    ensure!(
        i == job.intent
            && row.get::<_, Option<String>>(1).as_deref() == Some(&job.claim)
            && row.get::<_, bool>(2)
            && row.get::<_, bool>(3) == job.checkout_dispatched
            && row.get::<_, String>(4) == "pending",
        "Stale source import claim"
    );
    Ok((lead, d))
}
fn notice(tx: &mut postgres::Transaction<'_>, d: &Document, i: &Intent, key: &str) -> Result<()> {
    let last: Option<String> = tx
        .query_one(
            "SELECT last_notice FROM service_team_imports WHERE team=$1 AND agent=$2",
            &[&i.team, &i.agent],
        )?
        .get(0);
    if last.as_deref() == Some(key) {
        return Ok(());
    }
    let mut d = d.clone();
    d.sequence = d
        .sequence
        .checked_add(1)
        .filter(|n| *n <= i64::MAX as u64)
        .context("Team inbox sequence exhausted")?;
    let m = Message {
        id:openworker_runtime::id("tm"), sequence:d.sequence, sender:"harness".into(), recipients:vec![d.lead.clone()], board:false,
        body:format!("Harness child source status (not a permission grant). Instance {}, operation {}: {}. Original checkout and sandbox retained. Inspect status before choosing a new action; no checkout is automatically repeated.", i.agent, i.workspace.operation_id, if key=="blocked" {"original credential, placement or team authority unavailable"} else {"import outcome unknown; child has not been admitted"}),
    };
    Tx(tx).append(&d.id, &m)?;
    Tx(tx).write(&d, false)?;
    tx.execute(
        "UPDATE service_team_imports SET last_notice=$3 WHERE team=$1 AND agent=$2",
        &[&i.team, &i.agent, &key],
    )?;
    Ok(())
}
impl PostgresStore {
    /// The original published artifact is resolved inside the same transaction
    /// as workspace reservation and first preparation dispatch. No I/O here.
    pub fn claim_team_import(&self, installation: &str) -> Result<Option<Job>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let installation = installation.to_owned();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let row = tx.query_opt("SELECT s.id,t.id,a.key FROM agent_teams t JOIN sessions s ON s.id=t.data->'roster'->'instances'->(t.data->>'lead')->>'session_id' JOIN service_operations o ON o.session_id=s.id JOIN service_catalog c ON c.kind='release' AND c.id=o.release_id JOIN service_team_sources src ON src.team=t.id AND src.status='ready' CROSS JOIN LATERAL jsonb_each(t.data->'roster'->'instances') a LEFT JOIN service_team_imports i ON i.team=t.id AND i.agent=a.key WHERE c.data->>'installation'=$1 AND (t.data->>'coding_selection')::boolean AND NOT coalesce((t.data->>'stopped')::boolean,false) AND a.value->>'phase'='reserved' AND (t.data->'roster'->'requests'->a.key->'source'->>'kind'='initial' OR EXISTS(SELECT 1 FROM service_team_submissions sub WHERE sub.team=t.id AND sub.id=t.data->'roster'->'requests'->a.key->'source'->>'id' AND sub.status='ready')) AND (coalesce(t.data->'sandbox_choices'->a.key->>'mode','team')='team' OR t.data->'placement_bindings' ? a.key) AND (i.team IS NULL OR (i.status='pending' AND i.next_check<=clock_timestamp())) ORDER BY t.id,a.key LIMIT 1 FOR UPDATE OF s SKIP LOCKED", &[&installation])?;
            let Some(row) = row else {tx.commit()?;return Ok(None)};
            let mut lead = db::locked(&mut tx,&row.get::<_,String>(0),None)?;
            let team: String = row.get(1);
            let agent: String = row.get(2);
            let mut d = Tx(&mut tx).load(&team)?;
            if d.stopped || !d.roster.instance(&agent).is_some_and(|a| a.phase == team_coordination::Phase::Reserved) {tx.commit()?;return Ok(None)}
            let existing = tx.query_opt("SELECT data,prepare_dispatched,checkout_dispatched,next_check<=clock_timestamp(),status FROM service_team_imports WHERE team=$1 AND agent=$2 FOR UPDATE", &[&team,&agent])?;
            if existing.as_ref().is_some_and(|r| !r.get::<_,bool>(3) || r.get::<_,String>(4)!="pending") {tx.commit()?;return Ok(None)}
            if crate::service_admin::current(&mut tx)?.configuration.admissions_paused {tx.commit()?;return Ok(None)}
            let prepare = existing.as_ref().is_none_or(|r| !r.get::<_,bool>(1));
            let checkout_dispatched = existing.as_ref().is_some_and(|r| r.get::<_,bool>(2));
            let i: Intent = if let Some(row) = existing {
                serde_json::from_value(row.get(0))?
            } else {
                let preflight = (|| {
                    let selected = placement(&mut tx,&lead,&d,&agent)?;
                    let choice=d.roster.request(&agent).and_then(|r|r.source.as_ref()).context("Missing selected source")?;
                    let source = match choice {Choice::Initial=>crate::service_team_sources::ready_snapshot(&mut tx,&lead,&d)?,Choice::Submission{id}=>crate::service_team_submissions::ready_snapshot(&mut tx,&lead,&d,id)?};
                    Ok::<_,anyhow::Error>((selected,source))
                })();
                let ((environment,root,profile_id,profile_digest),(manifest,bundle,artifact)) = match preflight {
                    Ok(p) => p,
                    Err(e) if e.downcast_ref::<postgres::Error>().is_some() => return Err(e),
                    Err(_) => {
                        // No import has been dispatched. Close just this unstarted
                        // reservation; one revoked credential must not terminate
                        // reconciliation for unrelated teams.
                        agent_team::apply(&mut Tx(&mut tx),&mut lead,"provision_failed",&json!({"agent_id":agent,"error":"Source admission denied before dispatch: original credential, source or selected placement unavailable"}),openworker_runtime::store::now_ms())?;
                        crate::team_execution::session_event(&mut tx,&lead.id,None,"team_source_import",json!({"agent_id":agent,"phase":"blocked_before_dispatch"}))?;
                        tx.commit()?;return Ok(None)
                    }
                };
                let outcome = agent_team::apply(&mut Tx(&mut tx),&mut lead,"workspace_dispatch",&json!({"agent_id":agent}),openworker_runtime::store::now_ms())?;
                let workspace: openworker_runtime::team_workspace::Intent = serde_json::from_value(outcome["intent"].clone())?;
                // A generic directory receipt must never be adopted as a source.
                ensure!(workspace.receipt.is_none() && workspace.error.is_none(), "Workspace already has an unrelated outcome");
                let Selection::Directory{path} = &workspace.request.selection else {anyhow::bail!("Source import requires an independent directory")};
                let transfer = Transfer {request:Import {operation_id:workspace.request.operation_id.clone(), scope_digest:workspace.request.scope_digest.clone(), manifest_digest:manifest.digest()?, directory:path.clone()},manifest,bundle};
                transfer.validate()?;
                let i = Intent {reception:None,team:team.clone(),agent:agent.clone(),lead:lead.id.clone(),choice:d.roster.request(&agent).and_then(|r|r.source.clone()).context("Missing original source choice")?,environment,root,profile_id,profile_digest,workspace:workspace.request,transfer,artifact};
                tx.execute("INSERT INTO service_team_imports(team,agent,data) VALUES($1,$2,$3)", &[&team,&agent,&json!(i)])?;
                d = Tx(&mut tx).load(&team)?;
                i
            };
            if let Err(error) = authority(&mut tx,&lead,&d,&i) {
                if error.downcast_ref::<postgres::Error>().is_some() {return Err(error)}
                tx.execute("UPDATE service_team_imports SET status='blocked',claim=NULL WHERE team=$1 AND agent=$2", &[&team,&agent])?;
                notice(&mut tx,&d,&i,"blocked")?;
                tx.commit()?;return Ok(None)
            }
            let claim = uuid::Uuid::new_v4().to_string();
            tx.execute("UPDATE service_team_imports SET prepare_dispatched=TRUE,claim=$3,next_check=clock_timestamp()+interval '180 seconds' WHERE team=$1 AND agent=$2", &[&team,&agent,&claim])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_source_import",json!({"agent_id":agent,"operation_id":i.workspace.operation_id,"phase":"preparing","dispatch":prepare,"checkout_dispatched":checkout_dispatched}))?;
            tx.commit()?;Ok(Some(Job {intent:i,claim,prepare,checkout_dispatched}))
        })
    }
    pub fn check_team_import(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let (lead, d) = original(&mut tx, &job)?;
            authority(&mut tx, &lead, &d, &job.intent)?;
            tx.commit()?;
            Ok(())
        })
    }
    /// Consume checkout dispatch before calling ToolRunner. A lost response must
    /// be inspected and conveyed to the lead, not treated as retry permission.
    pub fn dispatch_team_import_checkout(&self, job: &Job) -> Result<Job> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let (lead,d) = original(&mut tx,&job)?;
            authority(&mut tx,&lead,&d,&job.intent)?;
            ensure!(!job.checkout_dispatched,"Checkout dispatch already consumed");
            tx.execute("UPDATE service_team_imports SET checkout_dispatched=TRUE WHERE team=$1 AND agent=$2", &[&job.intent.team,&job.intent.agent])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_source_import",json!({"agent_id":job.intent.agent,"operation_id":job.intent.workspace.operation_id,"phase":"checkout_dispatched"}))?;
            tx.commit()?;Ok(Job {checkout_dispatched:true,..job})
        })
    }
    pub fn complete_team_import(&self, job: &Job, receipt: Receipt) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let (mut lead,d) = original(&mut tx,&job)?;
            authority(&mut tx,&lead,&d,&job.intent)?;
            ensure!(job.checkout_dispatched && receipt.request == job.intent.workspace,"Original checkout was not dispatched");
            if job.intent.reception.is_some() {
                crate::service_team_receptions::complete(&mut tx,&lead,&d,&job.intent,&receipt)?;
            } else {
            agent_team::apply(&mut Tx(&mut tx),&mut lead,"source_result",&json!({"agent_id":job.intent.agent,"receipt":receipt,"source":{"choice":job.intent.choice,"manifest":job.intent.transfer.manifest}}),openworker_runtime::store::now_ms())?;
            }

            tx.execute("UPDATE service_team_imports SET status='ready',claim=NULL WHERE team=$1 AND agent=$2", &[&job.intent.team,&job.intent.agent])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_source_import",json!({"agent_id":job.intent.agent,"operation_id":job.intent.workspace.operation_id,"phase":"ready","commit":job.intent.transfer.manifest.submitted_commit}))?;
            tx.commit()?;Ok(())
        })
    }
    pub fn defer_team_import(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let (lead,mut d) = original(&mut tx,&job)?;
            let blocked = match authority(&mut tx,&lead,&d,&job.intent) {
                Ok(()) => false,
                Err(e) if e.downcast_ref::<postgres::Error>().is_some() => return Err(e),
                Err(_) => true,
            };
            let phase = if blocked {"blocked"} else {"pending"};
            tx.execute("UPDATE service_team_imports SET status=$3,claim=NULL,next_check=clock_timestamp()+interval '30 seconds' WHERE team=$1 AND agent=$2", &[&job.intent.team,&job.intent.agent,&phase])?;
            if let Some(id)=&job.intent.reception {
                let reception=d.receptions.get_mut(id).context("Missing original reception")?;
                reception.uncertain=true;
                if blocked {reception.error=Some("Original receiving authority unavailable; work retained".into());}
                Tx(&mut tx).write(&d,false)?;
            }
            notice(&mut tx,&d,&job.intent,if blocked {"blocked"} else {"unknown"})?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_source_import",json!({"agent_id":job.intent.agent,"operation_id":job.intent.workspace.operation_id,"phase":if blocked {"blocked"} else {"outcome_unknown"},"inspection_only":true}))?;
            tx.commit()?;Ok(())
        })
    }
}
