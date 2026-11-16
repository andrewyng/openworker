use crate::{auth, db, PostgresStore};
use anyhow::{ensure, Result};
use openworker_runtime::{
    connections::Selection,
    safety::{Config, Finding},
};
use serde_json::json;
use std::collections::BTreeMap;
impl db::Database {
    pub(crate) fn input_safety_policy(
        &mut self,
        sid: &str,
    ) -> Result<(Config, BTreeMap<String, Selection>)> {
        let mut tx = self.client.transaction()?;
        auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::Submit,
            Some(sid),
            None,
        )?;
        let s = db::locked(&mut tx, sid, None)?;
        ensure!(!s.closed, "Session closed");
        tx.commit()?;
        Ok((s.safety, s.safety_models))
    }
    pub(crate) fn record_input_safety(
        &mut self,
        sid: Option<&str>,
        request: &str,
        findings: Vec<Finding>,
    ) -> Result<()> {
        ensure!(
            findings.len() <= 256 && request.len() <= 100,
            "Invalid input check metadata"
        );
        let mut tx = self.client.transaction()?;
        let identity = auth::guard(&mut tx, self.auth.as_ref(), auth::Action::Submit, sid, None)?;
        if let Some(sid) = sid {
            db::locked(&mut tx, sid, None)?;
            for finding in findings {
                let row = tx.query_one(
                    "UPDATE sessions SET cursor=cursor+1 WHERE id=$1 RETURNING cursor,epoch",
                    &[&sid],
                )?;
                let cursor: i64 = row.get(0);
                let epoch: i64 = row.get(1);
                let mut data = json!(finding);
                data["submission_id"] = json!(request);
                data["actor"] = json!(identity.as_ref().map(|p| &p.principal));
                tx.execute(
                    "INSERT INTO events VALUES($1,$2,NULL,'safety_evaluation',$3)",
                    &[&sid, &cursor, &data],
                )?;
                tx.execute("INSERT INTO audit(session_id,cursor,turn_id,kind,owner_epoch,actor) VALUES($1,$2,NULL,'safety_evaluation',$3,$4)",&[&sid,&cursor,&epoch,&identity.as_ref().map(|p|p.principal.as_str())])?;
            }
        } else {
            for finding in findings {
                auth::audit_target(
                    &mut tx,
                    identity.as_ref(),
                    auth::Action::Submit,
                    request,
                    &format!("guardrail:{}:{}", finding.check, finding.outcome),
                )?;
            }
        }
        tx.commit()?;
        Ok(())
    }
}
impl PostgresStore {
    pub fn input_safety_policy(&self, sid: &str) -> Result<(Config, BTreeMap<String, Selection>)> {
        let sid = sid.to_owned();
        self.call(move |db| db.input_safety_policy(&sid))
    }
    pub fn record_input_safety(
        &self,
        sid: Option<&str>,
        request: &str,
        findings: &[Finding],
    ) -> Result<()> {
        let sid = sid.map(String::from);
        let request = request.to_owned();
        let findings = findings.to_vec();
        self.call(move |db| db.record_input_safety(sid.as_deref(), &request, findings))
    }
}
