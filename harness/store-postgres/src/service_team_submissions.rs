//! Durable committed-revision export. Tool intent precedes controller I/O.
//! Claims fence original authority; lost dispatch is inspection-only forever.
use crate::{agent_team::Tx, auth, db, PostgresStore};
use anyhow::{ensure, Context, Result};
use openworker_artifacts::{Artifact, Binding, Expected, Origin, Proof, Registration};
use openworker_runtime::{
    agent_team::{Document, Message, Transaction},
    coding_handoff::{
        git::{Bundle, Export},
        workflow::Submission,
        ArtifactRef, Kind, Manifest,
    },
    environment,
    store::Session,
    team_coordination::Phase,
    team_workspace::Receipt,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Intent {
    pub request: Submission,
    pub lead: String,
    pub principal: String,
    pub environment: environment::Binding,
    pub root: String,
    pub profile_id: String,
    pub profile_digest: String,
    pub release_id: String,
    pub release_digest: String,
    pub artifact_binding: Binding,
    pub workspace: Receipt,
    pub export: Export,
    pub bundle: Option<Bundle>,
}
#[derive(Clone, Debug)]
pub struct Job {
    pub intent: Intent,
    pub dispatch: bool,
    pub claim: String,
}
pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_team_submissions(id TEXT PRIMARY KEY,team TEXT NOT NULL REFERENCES agent_teams(id),data JSONB NOT NULL,status TEXT NOT NULL DEFAULT 'pending',dispatched BOOLEAN NOT NULL DEFAULT FALSE,claim TEXT,next_check TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),last_notice TEXT);")?;
    Ok(())
}
fn document(tx: &mut postgres::Transaction<'_>, team: &str) -> Result<(Session, Document)> {
    // Read the lead ID without a team lock, then follow lead -> team order.
    // Never acquire a producer row after the team: its worker may hold that row
    // while waiting for the team to commit its original tool result.
    let value: serde_json::Value = tx
        .query_one("SELECT data FROM agent_teams WHERE id=$1", &[&team])?
        .get(0);
    let id = value["roster"]["instances"][value["lead"].as_str().context("Missing lead")?]
        ["session_id"]
        .as_str()
        .context("Missing lead session")?;
    let lead = db::locked(tx, id, None)?;
    let d = Tx(tx).load(team)?;
    ensure!(
        d.roster
            .instance(&d.lead)
            .is_some_and(|i| i.session_id == lead.id),
        "Original lead changed"
    );
    Ok((lead, d))
}
fn row(tx: &mut postgres::Transaction<'_>, id: &str) -> Result<Intent> {
    Ok(serde_json::from_value(
        tx.query_one(
            "SELECT data FROM service_team_submissions WHERE id=$1 FOR UPDATE",
            &[&id],
        )?
        .get(0),
    )?)
}
fn authority(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
    i: &Intent,
) -> Result<()> {
    i.request.validate()?;
    ensure!(
        d.id == i.request.producer.team_id && lead.id == i.lead && !d.stopped && d.coding_selection,
        "Original submission team unavailable"
    );
    let state = d
        .submissions
        .get(&i.request.id)
        .context("Original submission removed")?;
    ensure!(state.request == i.request, "Original submission changed");
    let (who, _) = crate::agent_team::lead_authority(tx, lead, d)?;
    let r = tx.query_one(
        "SELECT release_id FROM service_operations WHERE session_id=$1 AND status='complete'",
        &[&lead.id],
    )?;
    let release = crate::service_catalog::entry(tx, "release", &r.get::<_, String>(0))?;
    ensure!(
        who.principal == i.principal
            && release.id == i.release_id
            && release.digest == i.release_digest,
        "Submission principal or release changed"
    );
    let producer = &i.request.producer;
    ensure!(
        d.roster
            .instance(&producer.agent_id)
            .is_some_and(|a| a.session_id == producer.session_id && a.phase == Phase::Ready),
        "Submission producer is retired or replaced"
    );
    let (environment, root, profile, digest) =
        crate::service_team_imports::placement(tx, lead, d, &producer.agent_id)?;
    ensure!(
        environment == i.environment
            && std::path::Path::new(&root) == i.workspace.project_root
            && std::path::Path::new(&i.root) == i.workspace.path
            && profile == i.profile_id
            && digest == i.profile_digest,
        "Original producer placement changed"
    );
    ensure!(
        d.workspaces
            .get(&producer.agent_id)
            .and_then(|w| w.receipt.as_ref())
            == Some(&i.workspace),
        "Original producer checkout changed"
    );
    let source = d
        .source_imports
        .get(&producer.agent_id)
        .context("Missing acknowledged source")?;
    ensure!(
        source.manifest.digest()? == i.request.source_manifest,
        "Original submission source changed"
    );
    i.export.validate()?;
    Ok(())
}
fn fenced(tx: &mut postgres::Transaction<'_>, job: &Job, i: &Intent) -> Result<()> {
    let r=tx.query_one("SELECT claim,next_check>clock_timestamp(),status FROM service_team_submissions WHERE id=$1",&[&i.request.id])?;
    ensure!(
        *i == job.intent
            && r.get::<_, Option<String>>(0).as_deref() == Some(&job.claim)
            && r.get::<_, bool>(1)
            && r.get::<_, String>(2) != "ready",
        "Stale submission claim"
    );
    Ok(())
}
fn notice(tx: &mut postgres::Transaction<'_>, d: &Document, i: &Intent, key: &str) -> Result<()> {
    let old: Option<String> = tx
        .query_one(
            "SELECT last_notice FROM service_team_submissions WHERE id=$1",
            &[&i.request.id],
        )?
        .get(0);
    if old.as_deref() == Some(key) {
        return Ok(());
    }
    let mut d = d.clone();
    d.submissions
        .get_mut(&i.request.id)
        .context("Missing original submission")?
        .uncertain = key == "unknown";
    if key == "blocked" {
        d.submissions
            .get_mut(&i.request.id)
            .context("Missing submission")?
            .error = Some("Original submission authority unavailable; work retained".into());
    }
    d.sequence = d
        .sequence
        .checked_add(1)
        .filter(|n| *n <= i64::MAX as u64)
        .context("Inbox sequence exhausted")?;
    let body=match key {"ready"=>"immutable bundle published", "blocked"=>"original authority unavailable; original workspace retained", _=>"outcome unknown; inspect original operation before any new action; no export is automatically repeated"};
    let m = Message {
        id: openworker_runtime::id("tm"),
        sequence: d.sequence,
        sender: "harness".into(),
        body: format!(
            "Harness submission status (not permission or verification). Submission {}: {body}.",
            i.request.id
        ),
        recipients: vec![d.lead.clone(), i.request.producer.agent_id.clone()],
        board: false,
    };
    Tx(tx).append(&d.id, &m)?;
    Tx(tx).write(&d, false)?;
    tx.execute(
        "UPDATE service_team_submissions SET last_notice=$2 WHERE id=$1",
        &[&i.request.id, &key],
    )?;
    Ok(())
}
pub(crate) fn ready_snapshot(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
    id: &str,
) -> Result<(Manifest, Bundle, Artifact)> {
    // Published immutable bytes survive producer retirement. Consumption still
    // requires the current original lead credential and same-team catalog.
    let (who, _) = crate::agent_team::lead_authority(tx, lead, d)?;
    let i = row(tx, id)?;
    ensure!(
        d.coding_selection
            && i.lead == lead.id
            && i.request.producer.team_id == d.id
            && i.principal == who.principal,
        "Submission is not in this original team"
    );
    let state = d
        .submissions
        .get(id)
        .context("Unknown same-team submission")?;
    ensure!(state.request == i.request, "Submission request changed");
    let status: String = tx
        .query_one(
            "SELECT status FROM service_team_submissions WHERE id=$1",
            &[&id],
        )?
        .get(0);
    ensure!(status == "ready", "Submission is not published");
    let b = i.bundle.context("Missing original export")?;
    b.validate()?;
    ensure!(b.request == i.export, "Original submission export changed");
    let a_id: String = tx
        .query_one(
            "SELECT artifact_id FROM artifact_requests WHERE session_id=$1 AND request_id=$2",
            &[&i.request.producer.session_id, &id],
        )?
        .get(0);
    let a = crate::artifacts::load(tx, &i.request.producer.session_id, &a_id)?;
    ensure!(
        a.status == openworker_artifacts::Status::Ready
            && a.proof.is_some()
            && a.registration == registration(&i.request, &b)
            && a.binding == i.artifact_binding,
        "Original submitted artifact changed"
    );
    let manifest = state
        .manifest
        .clone()
        .context("No acknowledged published manifest")?;
    i.request.require_manifest(&manifest)?;
    ensure!(
        manifest.reports.is_empty()
            && manifest.artifact
                == (ArtifactRef {
                    session_id: i.request.producer.session_id.clone(),
                    artifact_id: a.id.clone(),
                    size: b.size,
                    sha256: b.sha256.clone()
                }),
        "Immutable manifest changed"
    );
    Ok((manifest, b, a))
}
fn registration(request: &Submission, b: &Bundle) -> Registration {
    Registration {
        origin: Origin::TeamSubmission {
            team_id: request.producer.team_id.clone(),
            submission_id: request.id.clone(),
        },
        request_id: request.id.clone(),
        session_id: request.producer.session_id.clone(),
        turn_id: String::new(),
        name: "submission.bundle".into(),
        content_type: "application/x-git-bundle".into(),
        expected: Expected {
            size: b.size,
            sha256: b.sha256.clone(),
        },
    }
}
pub(crate) fn authorize_artifact(
    tx: &mut postgres::Transaction<'_>,
    producer: &Session,
    registration: &Registration,
    binding: &Binding,
    actor: &str,
    claim: Option<&str>,
) -> Result<()> {
    let Origin::TeamSubmission {
        team_id,
        submission_id,
    } = &registration.origin
    else {
        anyhow::bail!("Not submission provenance")
    };
    let (lead, d) = document(tx, team_id)?;
    let i = row(tx, submission_id)?;
    let claim = claim.context("Submission label is not claim authority")?;
    let r=tx.query_one("SELECT claim,next_check>clock_timestamp(),status FROM service_team_submissions WHERE id=$1",&[submission_id])?;
    ensure!(
        r.get::<_, Option<String>>(0).as_deref() == Some(claim)
            && r.get::<_, bool>(1)
            && r.get::<_, String>(2) == "uploading",
        "Submission upload claim unavailable"
    );
    authority(tx, &lead, &d, &i)?;
    let b = i.bundle.as_ref().context("No validated export")?;
    b.validate()?;
    ensure!(
        producer.id == i.request.producer.session_id
            && *registration == self::registration(&i.request, b)
            && *binding == i.artifact_binding
            && actor == i.principal,
        "Submission artifact differs from original intent"
    );
    Ok(())
}
impl PostgresStore {
    pub fn claim_team_submission(&self, installation: &str) -> Result<Option<Job>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let installation = installation.to_owned();
        self.call(move|db| {
            let mut tx=db.client.transaction()?;
            let candidate=tx.query_opt("SELECT t.id,s.key FROM agent_teams t JOIN sessions l ON l.id=t.data->'roster'->'instances'->(t.data->>'lead')->>'session_id' JOIN service_operations o ON o.session_id=l.id JOIN service_catalog c ON c.kind='release' AND c.id=o.release_id CROSS JOIN LATERAL jsonb_each(coalesce(t.data->'submissions','{}'::jsonb)) s LEFT JOIN service_team_submissions q ON q.id=s.key WHERE c.data->>'installation'=$1 AND s.value->'manifest'='null'::jsonb AND coalesce(s.value->'error','null'::jsonb)='null'::jsonb AND (q.id IS NULL OR (q.status IN ('pending','exporting','unknown','uploading') AND q.next_check<=clock_timestamp())) ORDER BY t.id,s.key LIMIT 1",&[&installation])?;
            let Some(candidate)=candidate else {tx.commit()?;return Ok(None)};
            let team:String=candidate.get(0); let id:String=candidate.get(1);
            let (lead,mut d)=document(&mut tx,&team)?;
            let state=d.submissions.get(&id).context("Missing original request")?;
            if state.manifest.is_some() {tx.commit()?;return Ok(None)}
            if crate::service_admin::current(&mut tx)?.configuration.admissions_paused {tx.commit()?;return Ok(None)}
            let existing=tx.query_opt("SELECT data FROM service_team_submissions WHERE id=$1 FOR UPDATE",&[&id])?;
            let i:Intent=if let Some(row)=existing {serde_json::from_value(row.get(0))?} else {
                let prepared=(|| {
                let request=state.request.clone(); request.validate()?;
                let (who,_)=crate::agent_team::lead_authority(&mut tx,&lead,&d)?;
                let (environment,root,profile_id,profile_digest)=crate::service_team_imports::placement(&mut tx,&lead,&d,&request.producer.agent_id)?;
                let workspace=d.workspaces.get(&request.producer.agent_id).and_then(|w|w.receipt.clone()).context("Missing acknowledged producer checkout")?;
                workspace.validate_scope(std::path::Path::new(&root),&workspace.path)?;
                ensure!(workspace.independent_git.is_some(),"Submission requires an independent checkout");
                let root=workspace.path.to_str().context("Nonportable checkout path")?.to_owned();
                let directory=".".to_owned();
                let (_,_,source)=crate::service_team_sources::ready_snapshot(&mut tx,&lead,&d)?;
                let release_id:String=tx.query_one("SELECT release_id FROM service_operations WHERE session_id=$1 AND status='complete'",&[&lead.id])?.get(0);
                let release=crate::service_catalog::entry(&mut tx,"release",&release_id)?;
                let scope=openworker_runtime::bundle::digest(&serde_json::to_vec(&("team-submission-v1",&request,&environment,&workspace,&source.binding,&release.digest,&profile_digest))?);
                let export=Export{operation_id:id.clone(),scope_digest:scope,repository:request.repository.clone(),directory,object_format:request.object_format,base_commit:request.base_commit.clone(),submitted_commit:request.submitted_commit.clone()};
                let i=Intent{request,lead:lead.id.clone(),principal:who.principal,environment,root,profile_id,profile_digest,release_id,release_digest:release.digest,artifact_binding:source.binding,workspace,export,bundle:None};
                authority(&mut tx,&lead,&d,&i)?;
                Ok::<_,anyhow::Error>(i)

                })();
                let i=match prepared {
                    Ok(i)=>i,
                    Err(e) if e.downcast_ref::<postgres::Error>().is_some()=>return Err(e),
                    Err(_)=>{
                        let state=d.submissions.get_mut(&id).context("Missing original request")?;
                        state.error=Some("Original source, producer, credential or placement unavailable before export dispatch".into());
                        Tx(&mut tx).write(&d,false)?;
                        crate::team_execution::session_event(&mut tx,&lead.id,None,"team_submission",json!({"submission_id":id,"phase":"blocked_before_dispatch"}))?;
                        tx.commit()?;return Ok(None)
                    }
                };
                tx.execute("INSERT INTO service_team_submissions(id,team,data) VALUES($1,$2,$3)",&[&id,&team,&json!(i)])?; i
            };
            let owner=tx.query_one("SELECT dispatched,next_check<=clock_timestamp(),status FROM service_team_submissions WHERE id=$1",&[&id])?;
            if !owner.get::<_,bool>(1) || !["pending","exporting","unknown","uploading"].contains(&owner.get::<_,String>(2).as_str()) {tx.commit()?;return Ok(None)}
            if let Err(e)=authority(&mut tx,&lead,&d,&i) {
                if e.downcast_ref::<postgres::Error>().is_some() {return Err(e)}
                tx.execute("UPDATE service_team_submissions SET status='blocked',claim=NULL WHERE id=$1",&[&id])?;
                notice(&mut tx,&d,&i,"blocked")?; tx.commit()?; return Ok(None)
            }
            let dispatch=!owner.get::<_,bool>(0); let claim=uuid::Uuid::new_v4().to_string();
            let phase=if i.bundle.is_some(){"uploading"}else{"exporting"};
            tx.execute("UPDATE service_team_submissions SET dispatched=TRUE,claim=$2,next_check=clock_timestamp()+interval '180 seconds',status=$3 WHERE id=$1",&[&id,&claim,&phase])?;
            crate::team_execution::session_event(&mut tx,&i.lead,None,"team_submission",json!({"submission_id":id,"phase":phase,"dispatch":dispatch}))?;
            tx.commit()?; Ok(Some(Job{intent:i,dispatch,claim}))
        })
    }
    pub fn check_team_submission(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let (lead, d) = document(&mut tx, &job.intent.request.producer.team_id)?;
            let i = row(&mut tx, &job.intent.request.id)?;
            fenced(&mut tx, &job, &i)?;
            authority(&mut tx, &lead, &d, &i)?;
            tx.commit()?;
            Ok(())
        })
    }
    pub fn observe_team_submission(&self, job: &Job, bundle: Bundle) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        bundle.validate()?;
        ensure!(
            bundle.request == job.intent.export,
            "Changed export receipt"
        );
        let job = job.clone();
        self.call(move|db|{let mut tx=db.client.transaction()?;let (lead,d)=document(&mut tx,&job.intent.request.producer.team_id)?;let mut i=row(&mut tx,&job.intent.request.id)?;fenced(&mut tx,&job,&i)?;authority(&mut tx,&lead,&d,&i)?;ensure!(i.bundle.as_ref().is_none_or(|b|*b==bundle),"Original bundle changed");i.bundle=Some(bundle);tx.execute("UPDATE service_team_submissions SET data=$2,status='uploading',claim=NULL,next_check=clock_timestamp() WHERE id=$1",&[&i.request.id,&json!(i)])?;tx.commit()?;Ok(())})
    }
    pub fn defer_team_submission(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move|db|{let mut tx=db.client.transaction()?;let (lead,d)=document(&mut tx,&job.intent.request.producer.team_id)?;let i=row(&mut tx,&job.intent.request.id)?;fenced(&mut tx,&job,&i)?;
        let blocked=match authority(&mut tx,&lead,&d,&i){Ok(())=>false,Err(e) if e.downcast_ref::<postgres::Error>().is_some()=>return Err(e),Err(_)=>true};
        let phase=if blocked{"blocked"}else if i.bundle.is_some(){"uploading"}else{"unknown"};
        tx.execute("UPDATE service_team_submissions SET status=$2,claim=NULL,next_check=clock_timestamp()+interval '30 seconds' WHERE id=$1",&[&i.request.id,&phase])?;
        notice(&mut tx,&d,&i,if blocked{"blocked"}else{"unknown"})?;
        crate::team_execution::session_event(&mut tx,&i.lead,None,"team_submission",json!({"submission_id":i.request.id,"phase":phase,"inspection_only":true}))?;tx.commit()?;Ok(())})
    }
    pub fn register_team_submission_artifact(&self, job: &Job) -> Result<Artifact> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let i = &job.intent;
            db.register_artifact_inner(
                registration(&i.request, i.bundle.as_ref().context("Not exported")?),
                i.artifact_binding.clone(),
                &i.principal,
                Some(&job.claim),
            )
        })
    }
    pub fn publish_team_submission_artifact(
        &self,
        job: &Job,
        id: &str,
        proof: Proof,
    ) -> Result<Artifact> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (job, id) = (job.clone(), id.to_owned());
        self.call(move |db| {
            db.publish_artifact_inner(
                &job.intent.request.producer.session_id,
                &id,
                proof,
                &job.intent.principal,
                Some(&job.claim),
            )
        })
    }
    pub fn complete_team_submission(&self, job: &Job, artifact_id: &str) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (job, id) = (job.clone(), artifact_id.to_owned());
        self.call(move|db|{let mut tx=db.client.transaction()?;let (lead,mut d)=document(&mut tx,&job.intent.request.producer.team_id)?;let i=row(&mut tx,&job.intent.request.id)?;fenced(&mut tx,&job,&i)?;authority(&mut tx,&lead,&d,&i)?;
            let b=i.bundle.as_ref().context("Missing export")?;
            let a=crate::artifacts::load(&mut tx,&i.request.producer.session_id,&id)?;
            ensure!(a.status==openworker_artifacts::Status::Ready && a.proof.is_some() && a.registration==registration(&i.request,b) && a.binding==i.artifact_binding,"Original artifact not published");
            let manifest=Manifest{version:1,kind:Kind::Submission,repository:i.request.repository.clone(),producer:i.request.producer.clone(),object_format:i.request.object_format,base_commit:i.request.base_commit.clone(),submitted_commit:i.request.submitted_commit.clone(),artifact:ArtifactRef{session_id:i.request.producer.session_id.clone(),artifact_id:a.id,size:b.size,sha256:b.sha256.clone()},reports:vec![]};
            i.request.require_manifest(&manifest)?;
            let state=d.submissions.get_mut(&i.request.id).context("Missing original submission")?;
            ensure!(state.manifest.as_ref().is_none_or(|m|*m==manifest),"Published manifest changed");state.manifest=Some(manifest);state.uncertain=false;
            Tx(&mut tx).write(&d,false)?;
            tx.execute("UPDATE service_team_submissions SET status='ready',claim=NULL WHERE id=$1",&[&i.request.id])?;
            notice(&mut tx,&d,&i,"ready")?;
            crate::team_execution::session_event(&mut tx,&i.lead,None,"team_submission",json!({"submission_id":i.request.id,"phase":"ready","commit":i.request.submitted_commit}))?;tx.commit()?;Ok(())})
    }
}
