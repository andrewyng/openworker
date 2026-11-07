//! App-owned lead checkout. The original source outbox owns both dispatch bits.
use super::service_team_sources::{authority, document, fenced, row, Job};
use crate::{auth, db, PostgresStore};
use anyhow::{ensure, Context, Result};
use openworker_runtime::coding_handoff::{
    transfer::{Receipt, Transfer},
    ArtifactRef, Import, Kind, Manifest, Producer,
};
use serde_json::{json, Value};
fn transfer(
    i: &super::service_team_sources::Intent,
    d: &openworker_runtime::agent_team::Document,
    a: &openworker_artifacts::Artifact,
) -> Result<Transfer> {
    let manifest = Manifest {
        version: 1,
        kind: Kind::Source,
        repository: i.source.repository.clone(),
        producer: Producer {
            team_id: i.team.clone(),
            agent_id: d.lead.clone(),
            session_id: i.lead.clone(),
        },
        object_format: i.source.object_format,
        base_commit: i.source.commit.clone(),
        submitted_commit: i.source.commit.clone(),
        artifact: ArtifactRef {
            session_id: i.lead.clone(),
            artifact_id: a.id.clone(),
            size: a.registration.expected.size,
            sha256: a.registration.expected.sha256.clone(),
        },
        reports: vec![],
    };
    let transfer = Transfer {
        request: Import {
            operation_id: format!("lead_{}", i.id),
            scope_digest: i.export.scope_digest.clone(),
            manifest_digest: manifest.digest()?,
            directory: i.source.directory.clone(),
        },
        manifest,
        bundle: i.bundle.clone().context("Source export unavailable")?,
    };
    transfer.validate()?;
    Ok(transfer)
}
impl PostgresStore {
    /// No prepare or checkout is dispatched twice, including after node loss.
    pub fn prepare_team_source_checkout(
        &self,
        job: &Job,
        artifact_id: &str,
    ) -> Result<(Transfer, openworker_artifacts::Artifact, bool, bool)> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (job, id) = (job.clone(), artifact_id.to_owned());
        self.call(move|db|{let mut tx=db.client.transaction()?;let lead=db::locked(&mut tx,&job.intent.lead,None)?;let d=document(&mut tx,&lead)?;let i=row(&mut tx,&d.id)?;fenced(&mut tx,&job,&i)?;authority(&mut tx,&lead,&d,&i)?;
            ensure!(i.source.remote_ref.is_some(),"Only remote setup imports into lead workspace");
            let a=crate::artifacts::load(&mut tx,&i.lead,&id)?;
            super::service_team_sources::authorize_artifact(&mut tx,&lead,&a.registration,&a.binding,&i.principal,Some(&job.claim))?;
            ensure!(a.status==openworker_artifacts::Status::Ready&&a.proof.is_some(),"Source artifact unavailable");
            let flags=tx.query_one("SELECT prepare_dispatched,checkout_dispatched FROM service_team_sources WHERE team=$1",&[&i.team])?;
            let t=transfer(&i,&d,&a)?;
            tx.execute("UPDATE service_team_sources SET prepare_dispatched=TRUE WHERE team=$1",&[&i.team])?;
            tx.commit()?;Ok((t,a,!flags.get::<_,bool>(0),flags.get(1)))})
    }
    pub fn dispatch_team_source_checkout(&self, job: &Job) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let job = job.clone();
        self.call(move|db|{let mut tx=db.client.transaction()?;let lead=db::locked(&mut tx,&job.intent.lead,None)?;let d=document(&mut tx,&lead)?;let i=row(&mut tx,&d.id)?;fenced(&mut tx,&job,&i)?;authority(&mut tx,&lead,&d,&i)?;
            ensure!(i.source.remote_ref.is_some(),"Not remote source setup");
            ensure!(tx.execute("UPDATE service_team_sources SET checkout_dispatched=TRUE WHERE team=$1 AND prepare_dispatched AND NOT checkout_dispatched",&[&i.team])?==1,"Original source checkout already dispatched; inspect only");tx.commit()?;Ok(())})
    }
    pub fn observe_team_source_checkout(
        &self,
        job: &Job,
        artifact_id: &str,
        receipt: Receipt,
    ) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (job, id) = (job.clone(), artifact_id.to_owned());
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let lead = db::locked(&mut tx, &job.intent.lead, None)?;
            let d = document(&mut tx, &lead)?;
            let i = row(&mut tx, &d.id)?;
            fenced(&mut tx, &job, &i)?;
            authority(&mut tx, &lead, &d, &i)?;
            ensure!(i.source.remote_ref.is_some(), "Not remote source setup");
            let a = crate::artifacts::load(&mut tx, &i.lead, &id)?;
            super::service_team_sources::authorize_artifact(
                &mut tx,
                &lead,
                &a.registration,
                &a.binding,
                &i.principal,
                Some(&job.claim),
            )?;
            let expected = transfer(&i, &d, &a)?;
            ensure!(
                receipt.transfer_digest == expected.digest()?
                    && receipt.commit == i.source.commit
                    && receipt.path == std::path::Path::new(&i.root).join(&i.source.directory),
                "Original source checkout receipt changed"
            );
            let flags = tx.query_one(
                "SELECT checkout_dispatched,checkout FROM service_team_sources WHERE team=$1",
                &[&i.team],
            )?;
            ensure!(
                flags.get::<_, bool>(0),
                "Source checkout was not dispatched"
            );
            let old: Option<Value> = flags.get(1);
            ensure!(
                old.is_none() || old == Some(json!(receipt)),
                "Source checkout receipt changed"
            );
            tx.execute(
                "UPDATE service_team_sources SET checkout=$2 WHERE team=$1",
                &[&i.team, &json!(receipt)],
            )?;
            tx.commit()?;
            Ok(())
        })
    }
    pub fn team_source_status(&self, sid: &str) -> Result<Option<Value>> {
        let sid = sid.to_owned();
        self.call(move|db|{let mut tx=db.client.transaction()?;auth::guard(&mut tx,db.auth.as_ref(),auth::Action::Read,Some(&sid),None)?;
            let value=tx.query_opt("SELECT data->>'id',status,data->'source' FROM service_team_sources WHERE data->>'lead'=$1",&[&sid])?.map(|r|json!({"id":r.get::<_,String>(0),"status":r.get::<_,String>(1),"source":r.get::<_,Value>(2)}));tx.commit()?;Ok(value)})
    }
}
pub(crate) fn ready_for_turn(tx: &mut postgres::Transaction<'_>, sid: &str) -> Result<bool> {
    Ok(tx.query_opt("SELECT status='ready' FROM service_team_sources WHERE data->>'lead'=$1 AND data->'source'->>'remote_ref' IS NOT NULL",&[&sid])?.is_none_or(|r|r.get(0)))
}
