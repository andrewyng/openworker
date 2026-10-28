//! Desired capacity is separate from observed Kubernetes state and infrastructure ownership.
use crate::{
    auth::{self, Action},
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Ownership {
    Manual,
    Autoscaled,
    Gitops,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Binding {
    pub id: String,
    pub namespace: String,
    pub deployment: String,
    pub uid: String,
    pub ownership: Ownership,
    pub max_replicas: i32,
}
impl Binding {
    pub fn validate(&self) -> Result<()> {
        for s in [&self.id, &self.namespace, &self.deployment, &self.uid] {
            crate::submissions::validate_id(s, 100)?;
        }
        ensure!(
            (1..=64).contains(&self.max_replicas),
            SubmissionError::InvalidRequest
        );
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Pool {
    pub binding: Binding,
    pub revision: i64,
    pub desired_replicas: i32,
    pub observed: Value,
}
pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_pools(id TEXT PRIMARY KEY,binding JSONB NOT NULL,revision BIGINT NOT NULL,desired_replicas INTEGER NOT NULL,observed JSONB NOT NULL DEFAULT '{}')")?;
    Ok(())
}
fn pool(r: &postgres::Row) -> Result<Pool> {
    Ok(Pool {
        binding: serde_json::from_value(r.get("binding"))?,
        revision: r.get("revision"),
        desired_replicas: r.get("desired_replicas"),
        observed: r.get("observed"),
    })
}
impl PostgresStore {
    /// Private controller registration. Public callers cannot retarget a Deployment or raise ceilings.
    pub fn service_bind_pool(&self, binding: Binding, initial: i32) -> Result<Pool> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        binding.validate()?;
        ensure!(
            (0..=binding.max_replicas).contains(&initial),
            SubmissionError::InvalidRequest
        );
        self.call(move|db|{let mut tx=db.client.transaction()?;
            tx.execute("INSERT INTO service_pools(id,binding,revision,desired_replicas) VALUES($1,$2,1,$3) ON CONFLICT DO NOTHING",&[&binding.id,&json!(binding),&initial])?;
            let p=pool(&tx.query_one("SELECT * FROM service_pools WHERE id=$1 FOR UPDATE",&[&binding.id])?)?;
            ensure!(p.binding==binding,SubmissionError::Conflict);tx.commit()?;Ok(p)})
    }
    pub fn service_pools(&self) -> Result<Vec<Pool>> {
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            auth::guard(&mut tx, db.auth.as_ref(), Action::Read, None, None)?;
            let result = tx
                .query("SELECT id,binding,revision,desired_replicas,CASE WHEN observed->>'status'='applied' AND coalesce((observed->>'checkedAt')::float8,0)<extract(epoch FROM clock_timestamp())-15 THEN observed || '{\"status\":\"unknown\",\"reason\":\"Controller observation is stale\"}'::jsonb ELSE observed END AS observed FROM service_pools ORDER BY id LIMIT 64", &[])?
                .iter()
                .map(pool)
                .collect::<Result<_>>()?;
            tx.commit()?;
            Ok(result)
        })
    }
    pub fn service_scale_pool(
        &self,
        id: &str,
        key: &str,
        expected: i64,
        replicas: i32,
    ) -> Result<Pool> {
        crate::submissions::validate_id(key, 100)?;
        let (id, key) = (id.to_owned(), key.to_owned());
        self.call(move|db|{let mut tx=db.client.transaction()?;let actor=auth::guard(&mut tx,db.auth.as_ref(),Action::Admin,None,None)?;
            let mut p=pool(&tx.query_opt("SELECT * FROM service_pools WHERE id=$1 FOR UPDATE",&[&id])?.ok_or(SubmissionError::NotFound)?)?;
            ensure!(p.binding.ownership==Ownership::Manual,SubmissionError::Conflict);
            let payload=json!({"pool":id,"expectedRevision":expected,"replicas":replicas,"actor":actor.as_ref().map(|a|&a.principal)});
            if let Some(r)=tx.query_opt("SELECT payload,result FROM service_management_receipts WHERE id=$1",&[&key])?{ensure!(r.get::<_,Value>(0)==payload,SubmissionError::Conflict);return Ok(serde_json::from_value(r.get(1))?);}
            ensure!(p.revision==expected,SubmissionError::Conflict);ensure!((0..=p.binding.max_replicas).contains(&replicas),SubmissionError::InvalidRequest);
            p.revision+=1;p.desired_replicas=replicas;p.observed=json!({"status":"pending","observedRevision":expected});
            tx.execute("UPDATE service_pools SET revision=$2,desired_replicas=$3,observed=$4 WHERE id=$1",&[&id,&p.revision,&replicas,&p.observed])?;
            tx.execute("INSERT INTO service_management_receipts VALUES($1,$2,$3)",&[&key,&payload,&json!(p)])?;
            auth::audit_target(&mut tx,actor.as_ref(),Action::Configure,&key,&id)?;tx.commit()?;Ok(p)})
    }
    pub fn service_observe_pool(&self, id: &str, revision: i64, observed: Value) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let id = id.to_owned();
        self.call(move |db| {
            db.client.execute(
                "UPDATE service_pools SET observed=$3 || jsonb_build_object('checkedAt',extract(epoch FROM clock_timestamp())) WHERE id=$1 AND revision=$2",
                &[&id, &revision, &observed],
            )?;
            Ok(())
        })
    }
}
