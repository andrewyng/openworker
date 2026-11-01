use crate::db::{locked, Database};
use anyhow::{ensure, Result};
use openworker_artifacts::{Artifact, Binding, Error, Proof, Registration, Status};
use postgres::Transaction;
use serde_json::{json, Value};
pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    let exists: bool = tx
        .query_one("SELECT to_regclass('artifact_version') IS NOT NULL", &[])?
        .get(0);
    if exists {
        check(tx)?;
    } else {
        for table in ["artifacts", "artifact_requests", "artifact_totals"] {
            let exists: bool = tx
                .query_one("SELECT to_regclass($1) IS NOT NULL", &[&table])?
                .get(0);
            ensure!(!exists, Error::Conflict);
        }
        tx.batch_execute("CREATE TABLE artifact_version(singleton BOOLEAN PRIMARY KEY CHECK(singleton),version INTEGER NOT NULL);INSERT INTO artifact_version VALUES(TRUE,1);
CREATE TABLE artifact_totals(session_id TEXT PRIMARY KEY REFERENCES sessions(id),total BIGINT NOT NULL);
CREATE TABLE artifacts(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),data JSONB NOT NULL,UNIQUE(session_id,id));
CREATE TABLE artifact_requests(session_id TEXT NOT NULL,request_id TEXT NOT NULL,artifact_id TEXT NOT NULL UNIQUE,actor TEXT NOT NULL,request JSONB NOT NULL,binding JSONB NOT NULL,PRIMARY KEY(session_id,request_id),FOREIGN KEY(session_id,artifact_id) REFERENCES artifacts(session_id,id));")?;
    }
    Ok(())
}
fn check(tx: &mut Transaction<'_>) -> Result<()> {
    let exists: bool = tx
        .query_one("SELECT to_regclass('artifact_version') IS NOT NULL", &[])?
        .get(0);
    ensure!(exists, Error::Unconfigured);
    let rows = tx.query("SELECT version FROM artifact_version WHERE singleton", &[])?;
    ensure!(
        rows.len() == 1 && rows[0].get::<_, i32>(0) == 1,
        Error::Conflict
    );
    Ok(())
}
fn verify(tx: &mut Transaction<'_>, sid: &str) -> Result<()> {
    check(tx)?;
    let row=tx.query_one("SELECT (SELECT count(*) FROM artifacts WHERE session_id=$1),(SELECT count(*) FROM artifact_requests WHERE session_id=$1),(SELECT total FROM artifact_totals WHERE session_id=$1),EXISTS(SELECT 1 FROM events WHERE session_id=$1 AND kind='artifact_pending')",&[&sid])?;
    let count: i64 = row.get(0);
    let requests: i64 = row.get(1);
    let saved: Option<i64> = row.get(2);
    let had: bool = row.get(3);
    ensure!(
        count == requests && saved.unwrap_or(0) == count && (!had || saved.is_some()),
        Error::Conflict
    );
    Ok(())
}
pub(crate) fn load(tx: &mut Transaction<'_>, sid: &str, id: &str) -> Result<Artifact> {
    let row = tx
        .query_opt(
            "SELECT data FROM artifacts WHERE session_id=$1 AND id=$2",
            &[&sid, &id],
        )?
        .ok_or(Error::NotFound)?;
    let a: Artifact = serde_json::from_value(row.get(0))?;
    a.validate()?;
    ensure!(
        a.id == id && a.registration.session_id == sid,
        Error::Conflict
    );
    let row=tx.query_opt("SELECT request,binding FROM artifact_requests WHERE session_id=$1 AND request_id=$2 AND artifact_id=$3",&[&sid,&a.registration.request_id,&id])?.ok_or(Error::Conflict)?;
    ensure!(
        row.get::<_, Value>(0) == serde_json::to_value(&a.registration)?
            && row.get::<_, Value>(1) == serde_json::to_value(&a.binding)?,
        Error::Conflict
    );
    Ok(a)
}
fn event(tx: &mut Transaction<'_>, a: &Artifact, kind: &str, actor: &str) -> Result<()> {
    let sid = &a.registration.session_id;
    let tid = a
        .registration
        .origin
        .is_turn()
        .then_some(a.registration.turn_id.as_str());
    let row = tx.query_one(
        "UPDATE sessions SET cursor=cursor+1 WHERE id=$1 RETURNING cursor,epoch",
        &[sid],
    )?;
    let cursor: i64 = row.get(0);
    let epoch: i64 = row.get(1);
    let data = json!({"artifact_id":a.id,"request_id":a.registration.request_id,"status":a.status});
    tx.execute(
        "INSERT INTO events(session_id,cursor,turn_id,kind,data) VALUES($1,$2,$3,$4,$5)",
        &[sid, &cursor, &tid, &kind, &data],
    )?;
    tx.execute("INSERT INTO audit(session_id,cursor,turn_id,kind,owner_epoch,actor) VALUES($1,$2,$3,$4,$5,$6)",&[sid,&cursor,&tid,&kind,&epoch,&actor])?;
    Ok(())
}
impl Database {
    pub(crate) fn register_artifact(
        &mut self,
        request: Registration,
        binding: Binding,
        actor: &str,
    ) -> Result<Artifact> {
        self.register_artifact_inner(request, binding, actor, None)
    }
    pub(crate) fn register_artifact_inner(
        &mut self,
        request: Registration,
        binding: Binding,
        actor: &str,
        source_claim: Option<&str>,
    ) -> Result<Artifact> {
        let mut tx = self.client.transaction()?;
        let p = crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Artifact,
            Some(&request.session_id),
            None,
        )?;
        crate::auth::actor(p.as_ref(), actor)?;
        // Public/generic registration cannot grant setup authority. Only the
        // private controller supplies its live original source-intent claim.
        ensure!(
            request.origin.is_turn() || (self.auth.is_none() && source_claim.is_some()),
            Error::Invalid
        );
        let s = locked(&mut tx, &request.session_id, None)?;
        if !request.origin.is_turn() {
            crate::service_team_sources::authorize_artifact(
                &mut tx,
                &s,
                &request,
                &binding,
                actor,
                source_claim,
            )?;
        }
        verify(&mut tx, &s.id)?;
        if let Some(row)=tx.query_opt("SELECT artifact_id,actor,request,binding FROM artifact_requests WHERE session_id=$1 AND request_id=$2",&[&s.id,&request.request_id])? {
            ensure!(row.get::<_,String>(1)==actor && row.get::<_,Value>(2)==serde_json::to_value(&request)? && row.get::<_,Value>(3)==serde_json::to_value(&binding)?,Error::Conflict);
            let a=load(&mut tx,&s.id,&row.get::<_,String>(0))?;
            crate::auth::audit(&mut tx,p.as_ref(),crate::auth::Action::Artifact,Some(&request.session_id),Some(&request.request_id))?;
            tx.commit()?;
            return Ok(a);
        }
        ensure!(!s.closed, Error::Conflict);
        let known = s.turn.as_ref().is_some_and(|t| t.id == request.turn_id)
            || tx
                .query_one(
                    "SELECT EXISTS(SELECT 1 FROM events WHERE session_id=$1 AND turn_id=$2)",
                    &[&s.id, &request.turn_id],
                )?
                .get::<_, bool>(0);
        ensure!(!request.origin.is_turn() || known, Error::NotFound);
        let used:bool=tx.query_one("SELECT EXISTS(SELECT 1 FROM events WHERE session_id=$1 AND kind='artifact_pending' AND data->>'request_id'=$2)",&[&s.id,&request.request_id])?.get(0);
        ensure!(!used, Error::Conflict);
        let count: i64 = tx
            .query_one(
                "SELECT count(*) FROM artifacts WHERE session_id=$1",
                &[&s.id],
            )?
            .get(0);
        ensure!(count < 256, Error::TooLarge);
        let id = format!("a_{}", uuid::Uuid::new_v4().simple());
        let key = format!("{}/{}/{}", binding.deployment, s.id, id);
        let a = Artifact {
            id,
            registration: request,
            binding,
            key,
            status: Status::Pending,
            proof: None,
        };
        a.validate()?;
        tx.execute(
            "INSERT INTO artifacts VALUES($1,$2,$3)",
            &[&a.id, &s.id, &serde_json::to_value(&a)?],
        )?;
        tx.execute(
            "INSERT INTO artifact_requests VALUES($1,$2,$3,$4,$5,$6)",
            &[
                &s.id,
                &a.registration.request_id,
                &a.id,
                &actor,
                &serde_json::to_value(&a.registration)?,
                &serde_json::to_value(&a.binding)?,
            ],
        )?;
        tx.execute("INSERT INTO artifact_totals VALUES($1,1) ON CONFLICT(session_id) DO UPDATE SET total=artifact_totals.total+1",&[&s.id])?;
        event(&mut tx, &a, "artifact_pending", actor)?;
        crate::auth::audit(
            &mut tx,
            p.as_ref(),
            crate::auth::Action::Artifact,
            Some(&a.registration.session_id),
            Some(&a.registration.request_id),
        )?;
        tx.commit()?;
        Ok(a)
    }
    pub(crate) fn artifact(&mut self, sid: &str, id: &str) -> Result<Artifact> {
        let mut tx = self.client.transaction()?;
        crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Read,
            Some(sid),
            None,
        )?;
        locked(&mut tx, sid, None)?;
        verify(&mut tx, sid)?;
        let a = load(&mut tx, sid, id)?;
        tx.commit()?;
        Ok(a)
    }
    pub(crate) fn artifacts(&mut self, sid: &str) -> Result<Vec<Artifact>> {
        let mut tx = self.client.transaction()?;
        crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Read,
            Some(sid),
            None,
        )?;
        locked(&mut tx, sid, None)?;
        verify(&mut tx, sid)?;
        let ids: Vec<String> = tx
            .query(
                "SELECT id FROM artifacts WHERE session_id=$1 ORDER BY id",
                &[&sid],
            )?
            .iter()
            .map(|r| r.get(0))
            .collect();
        ensure!(ids.len() <= 256, Error::TooLarge);
        let mut result = vec![];
        for id in ids {
            result.push(load(&mut tx, sid, &id)?);
        }
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn publish_artifact(
        &mut self,
        sid: &str,
        id: &str,
        proof: Proof,
        actor: &str,
    ) -> Result<Artifact> {
        self.publish_artifact_inner(sid, id, proof, actor, None)
    }
    pub(crate) fn publish_artifact_inner(
        &mut self,
        sid: &str,
        id: &str,
        proof: Proof,
        actor: &str,
        source_claim: Option<&str>,
    ) -> Result<Artifact> {
        let mut tx = self.client.transaction()?;
        let p = crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Artifact,
            Some(sid),
            None,
        )?;
        crate::auth::actor(p.as_ref(), actor)?;
        let s = locked(&mut tx, sid, None)?;
        verify(&mut tx, sid)?;
        let mut a = load(&mut tx, sid, id)?;
        if !a.registration.origin.is_turn() {
            ensure!(
                self.auth.is_none() && source_claim.is_some(),
                Error::Invalid
            );
            crate::service_team_sources::authorize_artifact(
                &mut tx,
                &s,
                &a.registration,
                &a.binding,
                actor,
                source_claim,
            )?;
        }
        ensure!(proof.expected == a.registration.expected, Error::Conflict);
        if a.status == Status::Ready {
            ensure!(a.proof.as_ref() == Some(&proof), Error::Conflict);
            crate::auth::audit(
                &mut tx,
                p.as_ref(),
                crate::auth::Action::Artifact,
                Some(sid),
                Some(id),
            )?;
            tx.commit()?;
            return Ok(a);
        }
        a.status = Status::Ready;
        a.proof = Some(proof);
        a.validate()?;
        tx.execute(
            "UPDATE artifacts SET data=$2 WHERE id=$1",
            &[&id, &serde_json::to_value(&a)?],
        )?;
        event(&mut tx, &a, "artifact_ready", actor)?;
        crate::auth::audit(
            &mut tx,
            p.as_ref(),
            crate::auth::Action::Artifact,
            Some(sid),
            Some(id),
        )?;
        tx.commit()?;
        Ok(a)
    }
}
