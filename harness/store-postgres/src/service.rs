//! Service identity and read-only run views. No execution or retry decisions.
use crate::{
    auth,
    db::{locked, Database},
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use serde_json::{json, Value};

pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_identity(singleton BOOLEAN PRIMARY KEY CHECK(singleton), id TEXT NOT NULL, namespace TEXT NOT NULL)")?;
    Ok(())
}
impl PostgresStore {
    pub fn submit_service_turn(
        &self,
        request: crate::TurnSubmission,
    ) -> Result<crate::SubmissionReceipt> {
        request.validate()?;
        self.call(move |db| db.submit_turn_with_mode(request, true))
    }
    /// Trusted installation/startup only. Never changes an established binding.
    pub fn bind_service(&self, id: &str, namespace: &str) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (id, namespace) = (id.to_owned(), namespace.to_owned());
        ensure!(
            !id.is_empty() && id.len() <= 100 && !namespace.is_empty() && namespace.len() <= 80,
            SubmissionError::InvalidRequest
        );
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            tx.execute(
                "INSERT INTO service_identity VALUES(TRUE,$1,$2) ON CONFLICT DO NOTHING",
                &[&id, &namespace],
            )?;
            let r = tx.query_one(
                "SELECT id,namespace FROM service_identity WHERE singleton FOR SHARE",
                &[],
            )?;
            ensure!(
                r.get::<_, String>(0) == id && r.get::<_, String>(1) == namespace,
                auth::Error::Conflict
            );
            tx.commit()?;
            Ok(())
        })
    }
    /// Verify a restored service without writing to a recovery-only database.
    pub fn verify_service(&self, id: &str, namespace: &str) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (id, namespace) = (id.to_owned(), namespace.to_owned());
        self.call(move |db| {
            let row = db.client.query_opt(
                "SELECT id,namespace FROM service_identity WHERE singleton",
                &[],
            )?;
            ensure!(
                row.is_some_and(
                    |r| r.get::<_, String>(0) == id && r.get::<_, String>(1) == namespace
                ),
                auth::Error::Conflict
            );
            Ok(())
        })
    }
    pub fn service_close(&self, sid: &str) -> Result<Value> {
        let sid = sid.to_owned();
        self.call(move|db|{
        let mut tx=db.client.transaction()?;let p=auth::guard(&mut tx,db.auth.as_ref(),auth::Action::Control,Some(&sid),None)?;
        let mut s=locked(&mut tx,&sid,None)?;if s.closed{return Ok(json!({"sessionId":sid,"closed":true}))}
        ensure!(s.turn.as_ref().is_none_or(|t|openworker_runtime::store::terminal(&t.status)),SubmissionError::Busy);
        ensure!(!crate::controls::pending(&mut tx,&sid)?,SubmissionError::Busy);
        let active:bool=tx.query_one("SELECT EXISTS(SELECT 1 FROM submissions WHERE session_id=$1 AND status='queued') OR EXISTS(SELECT 1 FROM attachments WHERE session_id=$1 AND state<>'released')",&[&sid])?.get(0);ensure!(!active,SubmissionError::Busy);
        let epoch:i64=tx.query_opt("UPDATE sessions SET owner=$2,epoch=epoch+1,expires=clock_timestamp()+interval '30 seconds' WHERE id=$1 AND expires<=clock_timestamp() RETURNING epoch",&[&sid,&db.owner])?.ok_or(SubmissionError::Busy)?.get(0);
        s.closed=true;crate::db::write_metadata(&mut tx,&s)?;
        crate::db::event(&mut tx,&s,&db.owner,epoch,"session_closed",json!({"actor":p.as_ref().map(|p|&p.principal)}))?;
        tx.execute("UPDATE sessions SET expires=clock_timestamp() WHERE id=$1",&[&sid])?;tx.commit()?;Ok(json!({"sessionId":sid,"closed":true}))
    })
    }
    pub fn service_environment_list(&self) -> Result<Value> {
        self.call(move|db|{let mut tx=db.client.transaction()?;auth::guard(&mut tx,db.auth.as_ref(),auth::Action::Admin,None,None)?;let rows=tx.query("SELECT o.environment_id,o.project,o.principal,e.data->>'phase' FROM service_environment_owners o JOIN environments e ON e.id=o.environment_id ORDER BY o.environment_id LIMIT 1000",&[])?;let values:Vec<Value>=rows.into_iter().map(|r|json!({"id":r.get::<_,String>(0),"project":r.get::<_,String>(1),"principal":r.get::<_,String>(2),"phase":r.get::<_,String>(3)})).collect();tx.commit()?;Ok(json!({"environments":values}))})
    }
    pub fn service_run_id(&self, sid: &str, tid: &str) -> Result<Value> {
        let (sid, tid) = (sid.to_owned(), tid.to_owned());
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            auth::guard(
                &mut tx,
                db.auth.as_ref(),
                auth::Action::Read,
                Some(&sid),
                None,
            )?;
            let id: String = tx
                .query_opt(
                    "SELECT id FROM submissions WHERE session_id=$1 AND turn_id=$2",
                    &[&sid, &tid],
                )?
                .ok_or(SubmissionError::NotFound)?
                .get(0);
            tx.commit()?;
            db.service_run(&sid, &id)
        })
    }
    pub fn service_run(&self, sid: &str, submission: &str) -> Result<Value> {
        let (sid, submission) = (sid.to_owned(), submission.to_owned());
        self.call(move |db| db.service_run(&sid, &submission))
    }
}
impl Database {
    fn service_run(&mut self, sid: &str, submission: &str) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::Read,
            Some(sid),
            None,
        )?;
        let session = locked(&mut tx, sid, None)?;
        crate::submissions::verify(&mut tx, sid)?;
        let r = tx
            .query_opt(
                "SELECT turn_id,status FROM submissions WHERE session_id=$1 AND id=$2",
                &[&sid, &submission],
            )?
            .ok_or(SubmissionError::NotFound)?;
        let tid: String = r.get(0);
        let phase: String = r.get(1);
        // Results for older runs come from that run's journal, never the latest turn.
        let messages:Option<Value>=tx.query_opt("SELECT data->'messages' FROM events WHERE session_id=$1 AND turn_id=$2 AND kind='assistant_message' ORDER BY cursor DESC LIMIT 1",&[&sid,&tid])?.map(|r|r.get(0));
        let final_output = messages.as_ref().and_then(|m| {
            let texts = m
                .as_array()?
                .iter()
                .flat_map(|m| m["content"].as_array().into_iter().flatten())
                .filter_map(|c| c["text"].as_str())
                .collect::<Vec<_>>();
            (!texts.is_empty()).then(|| texts.join("\n"))
        });
        let pending: Vec<Value> = session
            .interactions
            .iter()
            .filter(|i| i.turn_id == tid && i.response.is_none())
            .map(|i| serde_json::to_value(i).expect("interaction"))
            .collect();
        let current = session.turn.as_ref().is_some_and(|t| t.id == tid);
        let waiting = current
            && session.turn.as_ref().is_some_and(|t| t.status == "waiting")
            && !pending.is_empty();
        let status = if waiting {
            "interrupted"
        } else {
            match phase.as_str() {
                "queued" | "started" => "running",
                "completed" => "completed",
                "cancelled" => "cancelled",
                // Keep durable crash recovery distinct from a failed provider/tool.
                "interrupted" => "interrupted",
                _ => "failed",
            }
        };
        let cursor: i64 = tx
            .query_one(
                "SELECT COALESCE(max(cursor),0) FROM events WHERE session_id=$1 AND turn_id=$2",
                &[&sid, &tid],
            )?
            .get(0);
        let value = json!({"status":status,"failure":if status=="failed" || phase=="interrupted" {Some(&phase)} else {None},"finalOutput":if status=="completed"{final_output}else{None},"interruptions":if waiting {pending}else{vec![]},"sessionId":sid,"runId":tid,"submissionId":submission,"cursor":cursor});
        tx.commit()?;
        Ok(value)
    }
}
