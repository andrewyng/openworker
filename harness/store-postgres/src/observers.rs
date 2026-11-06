use crate::db;
use anyhow::{Context, Result};
#[cfg(feature = "guardrails")]
use serde_json::json;
use serde_json::Value;
impl db::Database {
    pub(crate) fn pending_observers(&mut self, sid: &str) -> Result<Vec<Value>> {
        #[cfg(not(feature = "guardrails"))]
        {
            let _ = sid;
            Ok(vec![])
        }
        #[cfg(feature = "guardrails")]
        {
            let epoch = self.epoch(sid)?;
            let mut tx = self.client.transaction()?;
            db::locked(&mut tx, sid, Some((&self.owner, epoch)))?;
            let result=tx.query("SELECT cursor,hook_name,hook_version,hook,record,turn_id,attempts,next_ms FROM safety_observers WHERE session_id=$1 ORDER BY next_ms,cursor,hook_name LIMIT 64",&[&sid])?.into_iter().map(|r|json!({"cursor":r.get::<_,i64>(0),"name":r.get::<_,String>(1),"version":r.get::<_,String>(2),"hook":r.get::<_,Value>(3),"record":r.get::<_,Value>(4),"turnId":r.get::<_,Option<String>>(5),"attempts":r.get::<_,i32>(6),"nextMs":r.get::<_,i64>(7)})).collect();
            tx.commit()?;
            Ok(result)
        }
    }
    pub(crate) fn settle_observer(&mut self, sid: &str, r: &Value, success: bool) -> Result<()> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        db::locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let cursor = r["cursor"].as_i64().context("Invalid cursor")?;
        let name = r["name"].as_str().context("Invalid name")?;
        let version = r["version"].as_str().context("Invalid version")?;
        if success {
            tx.execute("DELETE FROM safety_observers WHERE session_id=$1 AND cursor=$2 AND hook_name=$3 AND hook_version=$4",&[&sid,&cursor,&name,&version])?;
        } else {
            let now = openworker_runtime::store::now_ms() as i64;
            tx.execute("UPDATE safety_observers SET attempts=LEAST(attempts+1,30),next_ms=$5::bigint+LEAST(300000,1000*(1<<LEAST(attempts,8))) WHERE session_id=$1 AND cursor=$2 AND hook_name=$3 AND hook_version=$4",&[&sid,&cursor,&name,&version,&now])?;
        }
        tx.commit()?;
        Ok(())
    }
}
