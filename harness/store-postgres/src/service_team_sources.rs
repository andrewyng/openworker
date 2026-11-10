//! Application-authorized source setup outbox. No model-facing capability.
//! Export dispatch is committed once; recovery inspects its original operation.
use crate::{auth, db, PostgresStore};
use anyhow::{ensure, Context, Result};
use openworker_artifacts::{Artifact, Binding, Expected, Origin, Proof, Registration};
use openworker_runtime::{
    agent_team::Document,
    coding_handoff::git::{Bundle, Export},
    environment,
    store::Session,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

pub use openworker_runtime::coding_handoff::config::Source;
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Intent {
    pub id: String,
    pub team: String,
    pub lead: String,
    pub principal: String,
    pub source: Source,
    pub environment: environment::Binding,
    pub root: String,
    pub profile_id: String,
    pub profile_digest: String,
    pub release_id: String,
    pub release_digest: String,
    pub artifact_binding: Binding,
    pub export: Export,
    pub bundle: Option<Bundle>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub repository_access: Option<openworker_runtime::coding_handoff::publication::Access>,
}
#[derive(Clone, Debug)]
pub struct Job {
    pub intent: Intent,
    pub dispatch: bool,
    pub claim: String,
}
pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_team_sources(team TEXT PRIMARY KEY REFERENCES agent_teams(id),data JSONB NOT NULL,status TEXT NOT NULL DEFAULT 'pending',dispatched BOOLEAN NOT NULL DEFAULT FALSE,claim TEXT,next_check TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());")?;
    tx.batch_execute("ALTER TABLE service_team_sources ADD COLUMN IF NOT EXISTS last_notice TEXT")?;
    tx.batch_execute("ALTER TABLE service_team_sources ADD COLUMN IF NOT EXISTS prepare_dispatched BOOLEAN NOT NULL DEFAULT FALSE, ADD COLUMN IF NOT EXISTS checkout_dispatched BOOLEAN NOT NULL DEFAULT FALSE, ADD COLUMN IF NOT EXISTS checkout JSONB")?;
    Ok(())
}
fn notice(
    tx: &mut postgres::Transaction<'_>,
    d: &Document,
    i: &Intent,
    key: &str,
    body: String,
) -> Result<()> {
    let last: Option<String> = tx
        .query_one(
            "SELECT last_notice FROM service_team_sources WHERE team=$1",
            &[&d.id],
        )?
        .get(0);
    if last.as_deref() == Some(key) {
        return Ok(());
    }
    let mut d = d.clone();
    d.sequence = d
        .sequence
        .checked_add(1)
        .filter(|s| *s <= i64::MAX as u64)
        .context("Team inbox sequence exhausted")?;
    let message = openworker_runtime::agent_team::Message {
        id: openworker_runtime::id("tm"),
        sequence: d.sequence,
        sender: "harness".into(),
        body: format!(
            "Harness source status (not a permission grant). Setup {}: {body}",
            i.id
        ),
        recipients: vec![d.lead.clone()],
        board: false,
    };
    tx.execute(
        "INSERT INTO agent_team_messages VALUES($1,$2,$3,$4)",
        &[
            &d.id,
            &(message.sequence as i64),
            &message.id,
            &json!(message),
        ],
    )?;
    tx.execute(
        "UPDATE agent_teams SET data=$2 WHERE id=$1",
        &[&d.id, &json!(d)],
    )?;
    tx.execute(
        "UPDATE service_team_sources SET last_notice=$2 WHERE team=$1",
        &[&d.id, &key],
    )?;
    Ok(())
}
pub(super) fn document(tx: &mut postgres::Transaction<'_>, lead: &Session) -> Result<Document> {
    let team = &lead
        .agent_team
        .as_ref()
        .context("Source setup needs a team lead")?
        .team_id;
    let d: Document = serde_json::from_value(
        tx.query_one(
            "SELECT data FROM agent_teams WHERE id=$1 FOR UPDATE",
            &[team],
        )?
        .get(0),
    )?;
    ensure!(
        d.roster
            .instance(&d.lead)
            .is_some_and(|i| i.session_id == lead.id),
        "Source setup team lead unavailable"
    );
    Ok(d)
}
pub(super) fn authority(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
    i: &Intent,
) -> Result<()> {
    ensure!(!d.stopped, "Source setup team stopped");
    ensure!(
        d.coding.as_ref().is_none_or(|c| c.source == i.source),
        "Original coding source changed"
    );
    let (who, _) = crate::agent_team::lead_authority(tx, lead, d)?;
    let row = tx.query_one("SELECT release_id,profile_id FROM service_operations WHERE session_id=$1 AND status='complete'", &[&lead.id])?;
    let r = crate::service_catalog::entry(tx, "release", &row.get::<_, String>(0))?;
    let p = crate::service_catalog::entry(tx, "profile", &row.get::<_, String>(1))?;
    ensure!(
        who.principal == i.principal
            && d.id == i.team
            && lead.id == i.lead
            && lead.environment.as_ref() == Some(&i.environment)
            && lead.workspace == i.root
            && r.id == i.release_id
            && r.digest == i.release_digest
            && p.id == i.profile_id
            && p.digest == i.profile_digest,
        "Original source authority or environment changed"
    );
    if let Some(access) = &i.repository_access {
        let current = d
            .repository_access
            .as_ref()
            .and_then(|c| c.profiles.get(&i.profile_id));
        ensure!(
            current == Some(access),
            "Original source repository grant changed"
        );
        let profile: crate::service_catalog::Profile = serde_json::from_value(p.definition)?;
        ensure!(
            profile.repository_access.as_deref() == Some(&access.grant),
            "Source profile grant changed"
        );
    }
    Ok(())
}
pub(super) fn row(tx: &mut postgres::Transaction<'_>, team: &str) -> Result<Intent> {
    Ok(serde_json::from_value(
        tx.query_one(
            "SELECT data FROM service_team_sources WHERE team=$1 FOR UPDATE",
            &[&team],
        )?
        .get(0),
    )?)
}
/// The trusted receiver resolves a named original setup, never a model-supplied
/// URL, storage key or artifact label. Caller holds lead then team locks.
pub(crate) fn ready_snapshot(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
) -> Result<(
    openworker_runtime::coding_handoff::Manifest,
    Bundle,
    Artifact,
)> {
    let i = row(tx, &d.id)?;
    authority(tx, lead, d, &i)?;
    let status: String = tx
        .query_one(
            "SELECT status FROM service_team_sources WHERE team=$1",
            &[&d.id],
        )?
        .get(0);
    ensure!(status == "ready", "Original source has not been published");
    let b = i.bundle.context("Missing original source export")?;
    b.validate()?;
    ensure!(b.request == i.export, "Source export binding changed");
    let id: String = tx
        .query_one(
            "SELECT artifact_id FROM artifact_requests WHERE session_id=$1 AND request_id=$2",
            &[&lead.id, &i.id],
        )?
        .get(0);
    let a = crate::artifacts::load(tx, &lead.id, &id)?;
    ensure!(
        a.status == openworker_artifacts::Status::Ready
            && a.proof.is_some()
            && a.registration.origin
                == (Origin::TeamSetup {
                    team_id: d.id.clone(),
                    setup_id: i.id.clone()
                })
            && a.registration.request_id == i.id
            && a.registration.turn_id.is_empty()
            && a.registration.name == "source.bundle"
            && a.registration.content_type == "application/x-git-bundle"
            && a.registration.expected
                == (Expected {
                    size: b.size,
                    sha256: b.sha256.clone()
                })
            && a.binding == i.artifact_binding,
        "Original source artifact changed"
    );
    let m = openworker_runtime::coding_handoff::Manifest {
        version: 1,
        kind: openworker_runtime::coding_handoff::Kind::Source,
        repository: i.source.repository,
        producer: openworker_runtime::coding_handoff::Producer {
            team_id: d.id.clone(),
            agent_id: d.lead.clone(),
            session_id: lead.id.clone(),
        },
        object_format: i.source.object_format,
        base_commit: i.source.commit.clone(),
        submitted_commit: i.source.commit,
        artifact: openworker_runtime::coding_handoff::ArtifactRef {
            session_id: lead.id.clone(),
            artifact_id: a.id.clone(),
            size: b.size,
            sha256: b.sha256.clone(),
        },
        reports: vec![],
    };
    m.validate()?;
    Ok((m, b, a))
}
pub(super) fn fenced(tx: &mut postgres::Transaction<'_>, job: &Job, i: &Intent) -> Result<()> {
    let row = tx.query_one(
        "SELECT claim,next_check>clock_timestamp(),status FROM service_team_sources WHERE team=$1",
        &[&i.team],
    )?;
    ensure!(
        *i == job.intent
            && row.get::<_, Option<String>>(0).as_deref() == Some(&job.claim)
            && row.get::<_, bool>(1)
            && row.get::<_, String>(2) != "ready",
        "Stale source setup claim"
    );
    Ok(())
}
/// Called only by the private artifact registration/publication path, after
/// locking the original lead. A setup label cannot supply this claim authority.
pub(crate) fn authorize_artifact(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    r: &Registration,
    binding: &Binding,
    actor: &str,
    claim: Option<&str>,
) -> Result<()> {
    let Origin::TeamSetup { team_id, setup_id } = &r.origin else {
        anyhow::bail!("Not setup provenance")
    };
    let claim = claim.context("Setup label is not an authority receipt")?;
    let d = document(tx, lead)?;
    let i = row(tx, &d.id)?;
    let owner = tx.query_one(
        "SELECT claim,next_check>clock_timestamp(),status FROM service_team_sources WHERE team=$1",
        &[&d.id],
    )?;
    ensure!(
        owner.get::<_, Option<String>>(0).as_deref() == Some(claim)
            && owner.get::<_, bool>(1)
            && owner.get::<_, String>(2) == "uploading",
        "Source transfer claim unavailable"
    );
    authority(tx, lead, &d, &i)?;
    let bundle = i
        .bundle
        .as_ref()
        .context("Source export has no validated receipt")?;
    bundle.validate()?;
    ensure!(
        bundle.request == i.export
            && team_id == &i.team
            && setup_id == &i.id
            && r.request_id == i.id
            && r.session_id == i.lead
            && r.turn_id.is_empty()
            && actor == i.principal
            && *binding == i.artifact_binding
            && r.name == "source.bundle"
            && r.content_type == "application/x-git-bundle"
            && r.expected
                == (Expected {
                    size: bundle.size,
                    sha256: bundle.sha256.clone()
                }),
        "Source artifact differs from original setup receipt"
    );
    Ok(())
}
pub(crate) fn configure(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
    source: Source,
    binding: Binding,
) -> Result<Intent> {
    source.validate()?;
    binding.validate()?;
    ensure!(
        d.roster
            .instance(&d.lead)
            .is_some_and(|i| i.session_id == lead.id),
        "Not the original team lead"
    );
    ensure!(
        d.coding.as_ref().is_none_or(|c| c.source == source),
        "Source setup differs from coding defaults"
    );
    ensure!(!d.stopped, "Source setup team stopped");
    let (who, _) = crate::agent_team::lead_authority(tx, lead, d)?;
    let sid = &lead.id;
    let origin=tx.query_one("SELECT release_id,profile_id FROM service_operations WHERE session_id=$1 AND status='complete'", &[&sid])?;
    let r = crate::service_catalog::entry(tx, "release", &origin.get::<_, String>(0))?;
    let p = crate::service_catalog::entry(tx, "profile", &origin.get::<_, String>(1))?;
    let repository_access = if let Some(reference) = &source.remote_ref {
        let access = d
            .repository_access
            .as_ref()
            .and_then(|c| c.profiles.get(&p.id))
            .context("Remote source requires an installed RuntimeProfile repository grant")?;
        ensure!(
            access
                .repositories
                .get(&source.repository)
                .is_some_and(|r| r.read_refs.contains(reference)),
            "Source repository/ref denied"
        );
        Some(access.clone())
    } else {
        None
    };
    if let Some(existing) = tx.query_opt(
        "SELECT data FROM service_team_sources WHERE team=$1 FOR UPDATE",
        &[&d.id],
    )? {
        let i: Intent = serde_json::from_value(existing.get(0))?;
        ensure!(
            i.source == source && i.artifact_binding == binding,
            "Changed source setup retry"
        );
        authority(tx, lead, d, &i)?;
        // An explicit application retry may requeue this SAME intent
        // after restoring authority. The dispatch bit is never reset.
        tx.execute("UPDATE service_team_sources SET status='pending',claim=NULL,last_notice=NULL,next_check=clock_timestamp() WHERE team=$1 AND status='blocked'", &[&i.team])?;

        return Ok(i);
    }
    ensure!(
        lead.turn.is_none(),
        "Configure source before starting the lead"
    );
    let environment = lead
        .environment
        .clone()
        .context("Lead has no environment")?;
    let root = lead.workspace.clone();
    let id = openworker_runtime::id("source");
    let scope = openworker_runtime::bundle::digest(&serde_json::to_vec(&(
        "team-source-v1",
        &d.id,
        &sid,
        &source,
        &binding,
        &environment,
        &root,
        &r.digest,
        &p.digest,
    ))?);
    let export = Export {
        operation_id: id.clone(),
        scope_digest: scope,
        repository: source.repository.clone(),
        directory: source.directory.clone(),
        object_format: source.object_format,
        base_commit: source.commit.clone(),
        submitted_commit: source.commit.clone(),
    };
    let i = Intent {
        id,
        team: d.id.clone(),
        lead: sid.clone(),
        principal: who.principal,
        source,
        environment,
        root,
        profile_id: p.id,
        profile_digest: p.digest,
        release_id: r.id,
        release_digest: r.digest,
        artifact_binding: binding,
        export,
        bundle: None,
        repository_access,
    };
    tx.execute(
        "INSERT INTO service_team_sources(team,data) VALUES($1,$2)",
        &[&i.team, &json!(i)],
    )?;
    crate::team_execution::session_event(
        tx,
        &i.lead,
        None,
        "team_source",
        json!({"setup_id":i.id,"phase":"pending","repository":i.source.repository,"commit":i.source.commit}),
    )?;
    Ok(i)
}
impl PostgresStore {
    /// Application setup, not a tool or a model approval. Artifact binding is
    /// supplied by the configured server adapter, never accepted in HTTP JSON.
    pub fn configure_team_source(
        &self,
        sid: &str,
        source: Source,
        binding: Binding,
    ) -> Result<Intent> {
        source.validate()?;
        binding.validate()?;
        let sid = sid.to_owned();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let principal = auth::guard(
                &mut tx,
                db.auth.as_ref(),
                auth::Action::Control,
                Some(&sid),
                None,
            )?;
            let lead = db::locked(&mut tx, &sid, None)?;
            let d = document(&mut tx, &lead)?;
            let i = configure(&mut tx, &lead, &d, source, binding)?;
            auth::audit(
                &mut tx,
                principal.as_ref(),
                auth::Action::Control,
                Some(&i.lead),
                Some(&i.id),
            )?;
            tx.commit()?;
            Ok(i)
        })
    }
    pub fn claim_team_source(&self, installation: &str) -> Result<Option<Job>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let installation = installation.to_owned();
        self.call(move |db| {
            let mut tx=db.client.transaction()?;
            let candidate=tx.query_opt("SELECT s.data FROM service_team_sources s JOIN service_catalog c ON c.kind='release' AND c.id=s.data->>'release_id' WHERE c.data->>'installation'=$1 AND s.status IN ('pending','exporting','unknown','uploading') AND s.next_check<=clock_timestamp() ORDER BY s.next_check,s.team LIMIT 1", &[&installation])?;
            let Some(candidate)=candidate else {tx.commit()?;return Ok(None)};
            let candidate:Intent=serde_json::from_value(candidate.get(0))?;
            let lead=db::locked(&mut tx,&candidate.lead,None)?;
            let d=document(&mut tx,&lead)?;
            let i=row(&mut tx,&d.id)?;
            if i!=candidate {tx.commit()?;return Ok(None)}
            let owner=tx.query_one("SELECT dispatched,next_check<=clock_timestamp() FROM service_team_sources WHERE team=$1",&[&d.id])?;
            if !owner.get::<_,bool>(1) {tx.commit()?;return Ok(None)}
            // Fresh credentials/releases are required even for staging reads.
            if let Err(e) = authority(&mut tx,&lead,&d,&i) {
                if e.downcast_ref::<postgres::Error>().is_some() { return Err(e) }
                tx.execute("UPDATE service_team_sources SET status='blocked',claim=NULL WHERE team=$1", &[&i.team])?;
                notice(&mut tx,&d,&i,"blocked","Original source credential, profile or team authority is unavailable. Source staging is blocked; a waiting child has not been admitted. Existing work is retained.".into())?;
                crate::team_execution::session_event(&mut tx,&i.lead,None,"team_source",json!({"setup_id":i.id,"phase":"blocked","reason":"Original source authority unavailable"}))?;
                tx.commit()?; return Ok(None)
            }
            if crate::service_admin::current(&mut tx)?.configuration.admissions_paused {tx.commit()?;return Ok(None)}
            let dispatch=!owner.get::<_,bool>(0);
            let claim=uuid::Uuid::new_v4().to_string();
            let phase=if i.bundle.is_some(){"uploading"}else{"exporting"};
            tx.execute("UPDATE service_team_sources SET dispatched=TRUE,claim=$2,next_check=clock_timestamp()+interval '180 seconds',status=$3 WHERE team=$1",&[&i.team,&claim,&phase])?;
            tx.commit()?;Ok(Some(Job {intent:i,dispatch,claim}))
        })
    }
    pub fn observe_team_source(&self, job: &Job, bundle: Option<Bundle>) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        if let Some(b) = &bundle {
            b.validate()?;
            ensure!(
                b.request == job.intent.export,
                "Changed source export receipt"
            )
        }
        self.call(move |db| {
            let mut tx=db.client.transaction()?;
            let lead=db::locked(&mut tx,&job.intent.lead,None)?;
            let d=document(&mut tx,&lead)?;
            let mut i=row(&mut tx,&d.id)?;
            fenced(&mut tx,&job,&i)?;
            authority(&mut tx,&lead,&d,&i)?;
            if let Some(old)=&i.bundle {ensure!(bundle.as_ref()==Some(old),"Original source receipt changed")}
            i.bundle=bundle;
            let phase=if i.bundle.is_some(){"uploading"}else{"unknown"};
            tx.execute("UPDATE service_team_sources SET data=$2,status=$3,claim=NULL,next_check=clock_timestamp()+interval '2 seconds' WHERE team=$1",&[&i.team,&json!(i),&phase])?;
            if i.bundle.is_none() {notice(&mut tx,&d,&i,"unknown_export","Export outcome is unknown. Inspect the original operation; no export is automatically repeated and waiting children have not been admitted.".into())?;}
            crate::team_execution::session_event(&mut tx,&i.lead,None,"team_source",json!({"setup_id":i.id,"phase":phase,"dispatch":job.dispatch}))?;
            tx.commit()?;Ok(())
        })
    }
    pub fn check_team_source(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let lead = db::locked(&mut tx, &job.intent.lead, None)?;
            let d = document(&mut tx, &lead)?;
            let i = row(&mut tx, &d.id)?;
            fenced(&mut tx, &job, &i)?;
            authority(&mut tx, &lead, &d, &i)?;
            tx.commit()?;
            Ok(())
        })
    }
    /// Keep the original resource/bundle after transport failure. This does not
    /// grant first-dispatch authority again or discard partially published data.
    pub fn defer_team_source(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let mut tx=db.client.transaction()?;
            let lead=db::locked(&mut tx,&job.intent.lead,None)?;
            let d=document(&mut tx,&lead)?;
            let i=row(&mut tx,&d.id)?;fenced(&mut tx,&job,&i)?;
            let phase=if i.bundle.is_some(){"uploading"}else{"unknown"};
            tx.execute("UPDATE service_team_sources SET status=$2,claim=NULL,next_check=clock_timestamp()+interval '30 seconds' WHERE team=$1", &[&i.team,&phase])?;
            let key=if i.bundle.is_some(){"unknown_upload"}else{"unknown_export"};
            notice(&mut tx,&d,&i,key,"Source transfer outcome is unknown. The original export/artifact identity is retained. Inspect status before any new action; this is not evidence of child completion.".into())?;
            crate::team_execution::session_event(&mut tx,&i.lead,None,"team_source",json!({"setup_id":i.id,"phase":"outcome_unknown","inspection_only":true}))?;
            tx.commit()?;Ok(())
        })
    }
    pub fn register_team_source_artifact(&self, job: &Job) -> Result<Artifact> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move |db| {
            let i = &job.intent;
            let b = i.bundle.as_ref().context("Source not exported")?;
            db.register_artifact_inner(
                Registration {
                    origin: Origin::TeamSetup {
                        team_id: i.team.clone(),
                        setup_id: i.id.clone(),
                    },
                    request_id: i.id.clone(),
                    session_id: i.lead.clone(),
                    turn_id: String::new(),
                    name: "source.bundle".into(),
                    content_type: "application/x-git-bundle".into(),
                    expected: Expected {
                        size: b.size,
                        sha256: b.sha256.clone(),
                    },
                },
                i.artifact_binding.clone(),
                &i.principal,
                Some(&job.claim),
            )
        })
    }
    pub fn publish_team_source_artifact(
        &self,
        job: &Job,
        artifact_id: &str,
        proof: Proof,
    ) -> Result<Artifact> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (job, id) = (job.clone(), artifact_id.to_owned());
        self.call(move |db| {
            db.publish_artifact_inner(
                &job.intent.lead,
                &id,
                proof,
                &job.intent.principal,
                Some(&job.claim),
            )
        })
    }
    pub fn complete_team_source(&self, job: &Job, artifact_id: &str) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (job, id) = (job.clone(), artifact_id.to_owned());
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let lead = db::locked(&mut tx, &job.intent.lead, None)?;
            let d = document(&mut tx, &lead)?;
            let i = row(&mut tx, &d.id)?;
            fenced(&mut tx, &job, &i)?;
            let a = crate::artifacts::load(&mut tx, &lead.id, &id)?;
            authorize_artifact(
                &mut tx,
                &lead,
                &a.registration,
                &a.binding,
                &i.principal,
                Some(&job.claim),
            )?;
            ensure!(
                a.status == openworker_artifacts::Status::Ready && a.proof.is_some(),
                "Source artifact not verified/published"
            );
            if i.source.remote_ref.is_some() {
                let checkout: Option<serde_json::Value> = tx
                    .query_one(
                        "SELECT checkout FROM service_team_sources WHERE team=$1",
                        &[&i.team],
                    )?
                    .get(0);
                ensure!(
                    checkout.is_some(),
                    "Remote source checkout has not been acknowledged"
                );
            }
            tx.execute(
                "UPDATE service_team_sources SET status='ready',claim=NULL WHERE team=$1",
                &[&i.team],
            )?;
            crate::team_execution::session_event(
                &mut tx,
                &i.lead,
                None,
                "team_source",
                json!({"setup_id":i.id,"phase":"ready","artifact_id":id,"commit":i.source.commit}),
            )?;
            tx.commit()?;
            Ok(())
        })
    }
}
