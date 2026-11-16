//! Versioned service defaults. Secret values and infrastructure manifests are not configuration.
use crate::{
    auth::{self, Action},
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use postgres::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase", deny_unknown_fields)]
pub struct Configuration {
    pub admissions_paused: bool,
    pub max_pending_operations: i64,
    pub max_environments: i64,
    pub max_environments_per_principal: i64,
    pub max_queued_turns: i64,
    pub compaction: openworker_runtime::compaction::Config,
}
impl Default for Configuration {
    fn default() -> Self {
        Self {
            admissions_paused: false,
            max_pending_operations: 128,
            max_environments: 64,
            max_environments_per_principal: 16,
            max_queued_turns: 100,
            compaction: Default::default(),
        }
    }
}
impl Configuration {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            (1..=128).contains(&self.max_pending_operations)
                && (1..=64).contains(&self.max_environments)
                && (1..=16).contains(&self.max_environments_per_principal)
                && (1..=100).contains(&self.max_queued_turns),
            SubmissionError::InvalidRequest
        );
        self.compaction.validate()?;
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Revision {
    pub revision: i64,
    pub configuration: Configuration,
}
pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    crate::service_pools::migrate(tx)?;
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_configuration(singleton BOOLEAN PRIMARY KEY CHECK(singleton),revision BIGINT NOT NULL,data JSONB NOT NULL)")?;
    tx.execute(
        "INSERT INTO service_configuration VALUES(TRUE,1,$1) ON CONFLICT DO NOTHING",
        &[&json!(Configuration::default())],
    )?;
    Ok(())
}
pub(crate) fn current(tx: &mut Transaction<'_>) -> Result<Revision> {
    let row = tx.query_one(
        "SELECT revision,data FROM service_configuration WHERE singleton FOR SHARE",
        &[],
    )?;
    let c: Configuration = serde_json::from_value(row.get(1))?;
    c.validate()?;
    Ok(Revision {
        revision: row.get(0),
        configuration: c,
    })
}
impl PostgresStore {
    pub fn service_configuration(&self) -> Result<Revision> {
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            auth::guard(&mut tx, db.auth.as_ref(), Action::Read, None, None)?;
            let value = current(&mut tx)?;
            tx.commit()?;
            Ok(value)
        })
    }
    pub fn service_configure(
        &self,
        key: &str,
        expected: i64,
        configuration: Configuration,
    ) -> Result<Revision> {
        configuration.validate()?;
        crate::submissions::validate_id(key, 100)?;
        ensure!(expected > 0, SubmissionError::InvalidRequest);
        let key = key.to_owned();
        self.call(move|db|{
            let mut tx=db.client.transaction()?;let p=auth::guard(&mut tx,db.auth.as_ref(),Action::Admin,None,None)?;
            // Serialize the receipt check and revision compare; replay remains valid after later edits.
            let row=tx.query_one("SELECT revision,data FROM service_configuration WHERE singleton FOR UPDATE",&[])?;
            let payload=json!({"configuration":configuration,"expectedRevision":expected,"actor":p.as_ref().map(|p|&p.principal)});
            if let Some(r)=tx.query_opt("SELECT payload,result FROM service_management_receipts WHERE id=$1",&[&key])?{ensure!(r.get::<_,Value>(0)==payload,SubmissionError::Conflict);return Ok(serde_json::from_value(r.get(1))?);}
            ensure!(row.get::<_,i64>(0)==expected,SubmissionError::Conflict);
            let result=Revision{revision:expected+1,configuration};
            tx.execute("UPDATE service_configuration SET revision=$1,data=$2 WHERE singleton",&[&result.revision,&json!(result.configuration)])?;
            tx.execute("INSERT INTO service_management_receipts VALUES($1,$2,$3)",&[&key,&payload,&json!(result)])?;
            auth::audit_target(&mut tx,p.as_ref(),Action::Configure,&key,"configuration")?;tx.commit()?;Ok(result)
        })
    }
    pub fn service_operation_configuration(&self, id: &str) -> Result<Revision> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let id = id.to_owned();
        self.call(move |db| {
            let row = db.client.query_one(
                "SELECT configuration FROM service_operations WHERE id=$1",
                &[&id],
            )?;
            Ok(serde_json::from_value(row.get(0))?)
        })
    }
}
