//! Non-owner session creation and coherent snapshot reads. API-scoped handles
//! authorize inside transactions; unscoped embedding/worker handles are trusted.
use crate::auth::{self, Action, Scope};
use crate::db::{append_history, event, locked, verify_history_count, write_metadata, Database};
use crate::SubmissionError;
use anyhow::{bail, Result};
use openworker_runtime::store::{terminal, Session};
use postgres::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionCreation {
    pub request_id: String,
    pub actor: String,
    pub parameters: Value,
}
impl SessionCreation {
    pub(crate) fn validate(&self) -> Result<()> {
        crate::submissions::validate_id(&self.request_id, 100)?;
        crate::submissions::validate_id(&self.actor, 128)?;
        if !self.parameters.is_object() || self.parameters.to_string().len() > 128_000 {
            return Err(SubmissionError::InvalidRequest.into());
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CreationReceipt {
    pub request_id: String,
    pub session_id: String,
    pub duplicate: bool,
}
fn lookup(tx: &mut Transaction<'_>, request: &SessionCreation) -> Result<Option<CreationReceipt>> {
    if let Some(row) = tx.query_opt(
        "SELECT actor,parameters,session_id FROM creations WHERE request_id=$1",
        &[&request.request_id],
    )? {
        if row.get::<_, String>(0) != request.actor || row.get::<_, Value>(1) != request.parameters
        {
            return Err(SubmissionError::Conflict.into());
        }
        let sid: String = row.get(2);
        locked(tx, &sid, None)?;
        let key: Option<String> = tx
            .query_one(
                "SELECT creation_request_id FROM sessions WHERE id=$1",
                &[&sid],
            )?
            .get(0);
        if key.as_deref() != Some(&request.request_id) {
            bail!("Creation identity linkage changed");
        }
        return Ok(Some(CreationReceipt {
            request_id: request.request_id.clone(),
            session_id: sid,
            duplicate: true,
        }));
    }
    if tx
        .query_one(
            "SELECT EXISTS(SELECT 1 FROM sessions WHERE creation_request_id=$1)",
            &[&request.request_id],
        )?
        .get::<_, bool>(0)
    {
        bail!("Creation receipt missing; stored data incomplete");
    }
    Ok(None)
}
impl Database {
    pub(crate) fn creation(&mut self, request: SessionCreation) -> Result<Option<CreationReceipt>> {
        let mut tx = self.client.transaction()?;
        let p = auth::guard(
            &mut tx,
            self.auth.as_ref(),
            Action::Create,
            None,
            request.parameters["environment_id"].as_str(),
        )?;
        auth::actor(p.as_ref(), &request.actor)?;
        let receipt = lookup(&mut tx, &request)?;
        if let (Some(p), Some(r)) = (&p, &receipt) {
            anyhow::ensure!(
                auth::session_allowed(&mut tx, p, &r.session_id)?,
                auth::Error::Forbidden
            );
        }
        tx.commit()?;
        Ok(receipt)
    }
    pub(crate) fn create_session(
        &mut self,
        request: SessionCreation,
        s: Session,
    ) -> Result<CreationReceipt> {
        if s.storage_revision != 0
            || s.turn.is_some()
            || s.closed
            || !s.history.is_empty()
            || !s.interactions.is_empty()
            || !s.approval_grants.is_empty()
            || s.sandbox.is_some()
            || !s.direct_processes.is_empty()
            || s.schema_digest.is_some()
        {
            bail!("Expected fresh prepared session");
        }
        let mut tx = self.client.transaction()?;
        let p = auth::guard(
            &mut tx,
            self.auth.as_ref(),
            Action::Create,
            None,
            request.parameters["environment_id"].as_str(),
        )?;
        auth::actor(p.as_ref(), &request.actor)?;
        let result = create_in_tx(&mut tx, &self.owner, request, s, p)?;
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn read_snapshot(&mut self, sid: &str) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        auth::guard(&mut tx, self.auth.as_ref(), Action::Read, Some(sid), None)?;
        let s = locked(&mut tx, sid, None)?;
        verify_history_count(&mut tx, sid)?;
        let cursor: i64 = tx
            .query_one("SELECT cursor FROM sessions WHERE id=$1", &[&sid])?
            .get(0);
        let count:i64=tx.query_one("SELECT count(*) FROM history WHERE session_id=$1 AND (data->>'role'='user' OR data->>'type'='message')",&[&sid])?.get(0);
        let mut messages = vec![];
        let mut bytes = 0;
        for row in tx.query("SELECT data FROM history WHERE session_id=$1 AND (data->>'role'='user' OR data->>'type'='message') ORDER BY seq DESC LIMIT 100",&[&sid])? {
            let value:Value=row.get(0);bytes+=value.to_string().len();if bytes>262144 {break;}messages.push(value);
        }
        messages.reverse();
        let attempts:Vec<Value>=tx.query("SELECT id,turn_id,status,text,truncated FROM attempts WHERE session_id=$1 ORDER BY ordinal DESC LIMIT 20",&[&sid])?.into_iter().map(|r|json!({"id":r.get::<_,String>(0),"turn_id":r.get::<_,String>(1),"status":r.get::<_,String>(2),"text":r.get::<_,String>(3),"truncated":r.get::<_,bool>(4)})).collect();
        let pending: Vec<_> = s
            .interactions
            .iter()
            .filter(|i| {
                i.response.is_none()
                    && s.turn
                        .as_ref()
                        .is_some_and(|t| t.id == i.turn_id && !terminal(&t.status))
            })
            .collect();
        let truncated = (messages.len() as i64) < count;
        let mut snapshot = json!({"session":{"id":s.id,"name":s.name,"coworker":s.coworker,"workspace":s.workspace,"environment":s.environment,"model":s.model,"model_provider":s.model_provider,"model_selection":s.model_selection,"reviewer_selection":s.reviewer_selection,"closed":s.closed,"permissions":s.permissions,"sandbox":s.execution_backend,"enforcement":s.enforcement()},"turn":s.turn,"cursor":cursor,"messages":messages,"history_truncated":truncated,"attempts":attempts,"interactions":pending,"mode":if s.scripted{"scripted-test"}else{"live"}});
        if s.package.planning {
            snapshot["plan"] = json!(s.plan);
        }
        tx.commit()?;
        Ok(snapshot)
    }
    /// Bounded operational projection. No prompts, tool arguments, credentials,
    /// provider payloads or histories in the session directory.
    pub(crate) fn session_page(&mut self, after: Option<&str>, limit: u16) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        let p = auth::guard(&mut tx, self.auth.as_ref(), Action::Read, None, None)?;
        let after = after.unwrap_or("");
        if let Some(p) = &p {
            if !after.is_empty() {
                anyhow::ensure!(
                    auth::session_allowed(&mut tx, p, after)?,
                    auth::Error::Forbidden
                );
            }
        }
        if !after.is_empty()
            && !tx
                .query_one(
                    "SELECT EXISTS(SELECT 1 FROM sessions WHERE id=$1)",
                    &[&after],
                )?
                .get::<_, bool>(0)
        {
            return Err(crate::SubmissionError::NotFound.into());
        }
        let owner = p.as_ref().and_then(|p| {
            if p.scope == Scope::Owned {
                Some(p.principal.clone())
            } else {
                None
            }
        });
        let ids: Option<Vec<String>> = p.as_ref().and_then(|p| {
            if let Scope::Sessions { ids } = &p.scope {
                Some(ids.clone())
            } else {
                None
            }
        });
        let filter = if owner.is_some() {
            "EXISTS(SELECT 1 FROM api_session_owners o WHERE o.session_id=s.id AND o.principal=$3)"
        } else {
            "$3::text IS NULL"
        };
        let sql=format!("SELECT jsonb_build_object(
          'id',id,'name',left(data->>'name',512),'coworker',left(data->>'coworker',128),
          'model',left(data->>'model',128),'status',COALESCE(data->'turn'->>'status','idle'),
          'turn_id',data->'turn'->>'id','closed',data->'closed','cursor',cursor,'created',created,
          'pending',(SELECT count(*) FROM jsonb_array_elements(COALESCE(data->'interactions','[]'::jsonb)) i
             WHERE i->>'response' IS NULL AND i->>'turn_id'=data->'turn'->>'id'
             AND data->'turn'->>'status' IN ('accepted','running','waiting','recoverable','executing','cancelling')),
          'recovered',EXISTS(SELECT 1 FROM events e WHERE e.session_id=s.id AND e.kind='turn_recovered'))
          FROM sessions s WHERE ($1='' OR (created,id)<(SELECT created,id FROM sessions WHERE id=$1))
          AND {filter} AND ($4::text[] IS NULL OR s.id=ANY($4))
          ORDER BY created DESC,id DESC LIMIT $2");
        let rows = tx.query(&sql, &[&after, &(i64::from(limit) + 1), &owner, &ids])?;
        let more = rows.len() > usize::from(limit);
        let sessions: Vec<Value> = rows
            .into_iter()
            .take(usize::from(limit))
            .map(|r| r.get(0))
            .collect();
        let next = if more {
            sessions.last().and_then(|s| s["id"].as_str())
        } else {
            None
        };
        let result = json!({"sessions":sessions,"next_after":next});
        tx.commit()?;
        Ok(result)
    }
}

/// Shared atomic session insertion used by service-controller admission too.
pub(crate) fn create_in_tx(
    tx: &mut Transaction<'_>,
    owner: &str,
    request: SessionCreation,
    s: Session,
    p: Option<auth::Identity>,
) -> Result<CreationReceipt> {
    tx.query_one(
        "SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext($1))",
        &[&format!("session-create:{}", request.request_id)],
    )?;
    if let Some(receipt) = lookup(tx, &request)? {
        if let Some(p) = &p {
            anyhow::ensure!(
                auth::session_allowed(tx, p, &receipt.session_id)?,
                auth::Error::Forbidden
            );
        }
        return Ok(receipt);
    }
    // API-created sessions are immediately claimable; creating a session
    // does not start a worker, model request or sandbox operation.
    tx.execute("INSERT INTO sessions(id,data,owner,epoch,expires,creation_request_id) VALUES($1,'{}',$2,0,clock_timestamp()+interval '5 minutes',$3)",&[&s.id,&owner,&request.request_id])?;
    append_history(tx, &s)?;
    write_metadata(tx, &s)?;
    if let Some(p) = &p {
        tx.execute(
            "INSERT INTO api_session_owners VALUES($1,$2)",
            &[&s.id, &p.principal],
        )?;
    }
    auth::audit(
        tx,
        p.as_ref(),
        Action::Create,
        Some(&s.id),
        Some(&request.request_id),
    )?;
    event(
        tx,
        &s,
        owner,
        0,
        "session_created",
        json!({"request_id":request.request_id,"actor":request.actor,"coworker":s.coworker,"environment":s.environment,"permissions":s.permissions,"resource_policy":s.resource_policy}),
    )?;
    tx.execute(
        "INSERT INTO creations(request_id,actor,parameters,session_id) VALUES($1,$2,$3,$4)",
        &[
            &request.request_id,
            &request.actor,
            &request.parameters,
            &s.id,
        ],
    )?;
    tx.execute(
        "UPDATE sessions SET expires=clock_timestamp() WHERE id=$1",
        &[&s.id],
    )?;
    Ok(CreationReceipt {
        request_id: request.request_id,
        session_id: s.id,
        duplicate: false,
    })
}
