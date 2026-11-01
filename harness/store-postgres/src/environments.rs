use crate::db::{event, locked, Database};
use anyhow::{bail, ensure, Context, Result};
use openworker_runtime::{
    environment::{Attachment, AttachmentState, Binding, Management, Phase, Record, Sharing, Spec},
    store::Session,
};
use postgres::{Row, Transaction};
use serde_json::{json, Value};

fn record(row: &Row) -> Result<Record> {
    let mut e: Record = serde_json::from_value(row.get("data"))?;
    e.revision = row.get("revision");
    Ok(e)
}
fn lock(tx: &mut Transaction<'_>, id: &str) -> Result<Record> {
    record(
        &tx.query_opt(
            "SELECT data,revision FROM environments WHERE id=$1 FOR UPDATE",
            &[&id],
        )?
        .context("Environment not found")?,
    )
}
fn change(tx: &mut Transaction<'_>, e: &mut Record, kind: &str, data: Value) -> Result<()> {
    let old = e.revision;
    e.revision += 1;
    if tx.execute(
        "UPDATE environments SET data=$2,revision=$3 WHERE id=$1 AND revision=$4",
        &[&e.id, &serde_json::to_value(&e)?, &e.revision, &old],
    )? != 1
    {
        bail!("Environment revision changed");
    }
    tx.execute(
        "INSERT INTO environment_events(environment_id,revision,kind,data) VALUES($1,$2,$3,$4)",
        &[&e.id, &e.revision, &kind, &data],
    )?;
    Ok(())
}
/// Reuse the original binding/revision checks inside an outbox transaction.
pub(crate) fn complete(
    tx: &mut Transaction<'_>,
    id: &str,
    revision: i64,
    binding: Option<Binding>,
) -> Result<Record> {
    let mut e = lock(tx, id)?;
    if let Some(b) = &binding {
        b.validate()?;
        if b.environment_id != e.id || b.spec != e.spec {
            bail!("Environment result does not match requested authority");
        }
    }
    if e.phase == Phase::Ready && e.binding == binding {
        return Ok(e);
    }
    if e.revision != revision || !matches!(e.phase, Phase::Provisioning | Phase::Unknown) {
        bail!("Stale environment operation");
    }
    e.phase = if binding.is_some() {
        Phase::Ready
    } else {
        Phase::Unknown
    };
    e.binding = binding;
    let known = e.binding.is_some();
    change(tx, &mut e, "environment_observed", json!({"known":known}))?;
    Ok(e)
}
fn attachment(row: &Row) -> Result<Attachment> {
    Ok(Attachment {
        id: row.get("id"),
        environment_id: row.get("environment_id"),
        session_id: row.get("session_id"),
        server: row.get("server"),
        owner_epoch: row.get("owner_epoch"),
        state: serde_json::from_value(json!(row.get::<_, String>("state")))?,
        outcome_unknown: row.get("outcome_unknown"),
    })
}

/// Always acquire the session before its environment. Delete never locks session
/// rows; the environment lock prevents a new binding racing the delete predicate.
pub(crate) fn validate_session(tx: &mut Transaction<'_>, s: &Session) -> Result<()> {
    let Some(binding) = &s.environment else {
        return Ok(());
    };
    binding.validate()?;
    if let Some(receipt) = &s.managed_workspace {
        receipt.validate_scope(
            std::path::Path::new(&binding.spec.workspace_root),
            std::path::Path::new(&s.workspace),
        )?;
        let member = s
            .agent_team
            .as_ref()
            .context("Managed remote workspace requires team membership")?;
        let doc: openworker_runtime::agent_team::Document = serde_json::from_value(
            tx.query_one(
                "SELECT data FROM agent_teams WHERE id=$1",
                &[&member.team_id],
            )?
            .get(0),
        )?;
        ensure!(
            doc.roster
                .instance(&member.agent_id)
                .is_some_and(|i| i.session_id == s.id)
                && doc
                    .workspaces
                    .get(&member.agent_id)
                    .and_then(|i| i.receipt.as_ref())
                    == Some(receipt),
            "Workspace receipt is not bound to this team instance"
        );
    } else if s.workspace != binding.spec.workspace_root {
        bail!("Child workspace requires an acknowledged team receipt");
    }
    if s.resource_policy.digest != binding.fingerprint()? {
        bail!("Session resource authority must match its remote environment binding");
    }
    if s.sandbox.is_some()
        || !s.direct_processes.is_empty()
        || s.execution_backend != openworker_runtime::execution::Backend::Openshell
    {
        bail!("Remote binding cannot mix with local execution/lifecycle");
    }
    if !s.resource_policy.files.is_empty()
        || !s.resource_policy.credentials.is_empty()
        || !s.resource_policy.network.is_empty()
        || s.resource_policy
            .servers
            .values()
            .any(|p| *p == openworker_runtime::resource_policy::Placement::Host)
    {
        bail!("Initial remote workspace profile cannot carry host/credential/network grants");
    }
    if let Some(member) = &s.agent_team {
        let doc: openworker_runtime::agent_team::Document = serde_json::from_value(
            tx.query_one(
                "SELECT data FROM agent_teams WHERE id=$1",
                &[&member.team_id],
            )?
            .get(0),
        )?;
        if member.agent_id != doc.lead {
            ensure!(
                doc.roster
                    .instance(&member.agent_id)
                    .is_some_and(|i| i.session_id == s.id),
                "Child is not the original team instance"
            );
            if doc
                .sandbox_choices
                .get(&member.agent_id)
                .is_some_and(|c| c != &Default::default())
            {
                let placement = doc
                    .placement_bindings
                    .get(&member.agent_id)
                    .context("Child placement has no original binding")?;
                ensure!(
                    doc.placement_selection
                        && Some(&placement.choice) == doc.sandbox_choices.get(&member.agent_id)
                        && &placement.environment == binding,
                    "Child placement authority changed"
                );
            }
        }
    }
    let e = lock(tx, &binding.environment_id)?;
    if e.binding.as_ref() != Some(binding) || (!s.closed && e.phase != Phase::Ready) {
        bail!("Environment unavailable or binding identity changed");
    }
    if binding.spec.sharing == Sharing::Exclusive && !s.closed {
        let occupied:bool=tx.query_one("SELECT EXISTS(SELECT 1 FROM sessions WHERE environment_id=$1 AND id<>$2 AND NOT (data->>'closed')::boolean)",&[&e.id,&s.id])?.get(0);
        if occupied {
            bail!("Exclusive environment already has a session");
        }
    }
    Ok(())
}
impl Database {
    pub(crate) fn begin_environment(
        &mut self,
        key: &str,
        actor: &str,
        spec: Spec,
    ) -> Result<(Record, bool)> {
        crate::submissions::validate_id(key, 100)?;
        crate::submissions::validate_id(actor, 128)?;
        spec.validate()?;
        let mut tx = self.client.transaction()?;
        // Transaction advisory key serializes competing first insertions only.
        tx.query_one(
            "SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext($1))",
            &[&key],
        )?;
        if let Some(row) = tx.query_opt(
            "SELECT data,revision FROM environments WHERE request_id=$1",
            &[&key],
        )? {
            let e = record(&row)?;
            if e.spec != spec || e.actor != actor {
                bail!("Environment idempotency conflict");
            }
            tx.commit()?;
            return Ok((e, false));
        }
        let e = Record {
            id: openworker_runtime::id("env"),
            request_id: key.into(),
            actor: actor.into(),
            spec,
            phase: Phase::Provisioning,
            binding: None,
            revision: 1,
        };
        tx.execute(
            "INSERT INTO environments(id,request_id,data) VALUES($1,$2,$3)",
            &[&e.id, &key, &serde_json::to_value(&e)?],
        )?;
        tx.execute("INSERT INTO environment_events(environment_id,revision,kind,data) VALUES($1,1,'environment_requested',$2)",&[&e.id,&json!({"actor":actor,"request_id":key})])?;
        tx.commit()?;
        Ok((e, true))
    }
    pub(crate) fn environment(&mut self, id: &str) -> Result<Record> {
        record(
            &self
                .client
                .query_opt("SELECT data,revision FROM environments WHERE id=$1", &[&id])?
                .context("Environment not found")?,
        )
    }
    pub(crate) fn complete_environment(
        &mut self,
        id: &str,
        revision: i64,
        binding: Option<Binding>,
    ) -> Result<Record> {
        let mut tx = self.client.transaction()?;
        let e = complete(&mut tx, id, revision, binding)?;
        tx.commit()?;
        Ok(e)
    }
    pub(crate) fn begin_environment_delete(
        &mut self,
        id: &str,
        key: &str,
        actor: &str,
    ) -> Result<(Record, bool)> {
        crate::submissions::validate_id(key, 100)?;
        crate::submissions::validate_id(actor, 128)?;
        let mut tx = self.client.transaction()?;
        let mut e = lock(&mut tx, id)?;
        if e.spec.management != Management::Harness {
            bail!("Environment lifecycle belongs to the application");
        }
        let row = tx.query_one(
            "SELECT delete_request_id,delete_actor FROM environments WHERE id=$1",
            &[&id],
        )?;
        if let Some(old) = row.get::<_, Option<String>>(0) {
            if old != key || row.get::<_, Option<String>>(1).as_deref() != Some(actor) {
                bail!("Environment delete idempotency conflict");
            }
            tx.commit()?;
            return Ok((e, false));
        }
        if e.phase != Phase::Ready {
            bail!("Reconcile environment before deletion");
        }
        let attached:bool=tx.query_one("SELECT EXISTS(SELECT 1 FROM sessions WHERE environment_id=$1 AND NOT (data->>'closed')::boolean) OR EXISTS(SELECT 1 FROM attachments WHERE environment_id=$1 AND state<>'released') OR EXISTS(SELECT 1 FROM agent_teams t CROSS JOIN LATERAL jsonb_each(t.data->'roster'->'instances') i WHERE i.value->>'phase' IN ('reserved','ready','retiring') AND (t.data->'placement_bindings'->i.key->'environment'->>'environment_id'=$1 OR (t.data->'sandbox_choices'->i.key->>'mode'='member' AND t.data->'placement_bindings'->(t.data->'sandbox_choices'->i.key->>'agent_id')->'environment'->>'environment_id'=$1))) OR EXISTS(SELECT 1 FROM service_team_environments o JOIN agent_teams t ON t.id=o.team WHERE o.environment_id=$1 AND t.data->'roster'->'instances'->o.agent->>'phase' IN ('reserved','ready','retiring'))",&[&id])?.get(0);
        if attached {
            bail!("Environment still has sessions or reserved/active attachments");
        }
        e.phase = Phase::Deleting;
        tx.execute(
            "UPDATE environments SET delete_request_id=$2,delete_actor=$3 WHERE id=$1",
            &[&id, &key, &actor],
        )?;
        change(
            &mut tx,
            &mut e,
            "environment_delete_requested",
            json!({"actor":actor,"request_id":key}),
        )?;
        tx.commit()?;
        Ok((e, true))
    }
    pub(crate) fn complete_environment_delete(
        &mut self,
        id: &str,
        key: &str,
        revision: i64,
    ) -> Result<Record> {
        let mut tx = self.client.transaction()?;
        let mut e = lock(&mut tx, id)?;
        let stored: Option<String> = tx
            .query_one(
                "SELECT delete_request_id FROM environments WHERE id=$1",
                &[&id],
            )?
            .get(0);
        if stored.as_deref() != Some(key) {
            bail!("Environment delete request mismatch");
        }
        if e.phase == Phase::Deleted {
            tx.commit()?;
            return Ok(e);
        }
        if e.phase != Phase::Deleting || e.revision != revision {
            bail!("Stale environment deletion");
        }
        e.phase = Phase::Deleted;
        change(
            &mut tx,
            &mut e,
            "environment_deleted",
            json!({"request_id":key}),
        )?;
        tx.commit()?;
        Ok(e)
    }
    pub(crate) fn reserve_attachment(
        &mut self,
        sid: &str,
        server: &str,
    ) -> Result<(Attachment, bool)> {
        crate::submissions::validate_id(server, 80)?;
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        if s.closed {
            bail!("Session closed");
        }
        validate_session(&mut tx, &s)?;
        let b = s
            .environment
            .as_ref()
            .context("Session has no remote environment")?;
        let mut e = lock(&mut tx, &b.environment_id)?;
        if let Some(row)=tx.query_opt("SELECT * FROM attachments WHERE session_id=$1 AND server=$2 AND owner_epoch=$3 AND state<>'released'",&[&sid,&server,&epoch])? {let a=attachment(&row)?;tx.commit()?;return Ok((a,false));}
        let id = uuid::Uuid::new_v4().to_string();
        let row=tx.query_one("INSERT INTO attachments(id,environment_id,session_id,server,owner_epoch,state) VALUES($1,$2,$3,$4,$5,'pending') RETURNING *",&[&id,&e.id,&sid,&server,&epoch])?;
        let a = attachment(&row)?;
        change(
            &mut tx,
            &mut e,
            "attachment_reserved",
            json!({"attachment_id":id,"session_id":sid,"epoch":epoch}),
        )?;
        event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "attachment_reserved",
            json!({"attachment_id":id,"environment_id":e.id,"server":server}),
        )?;
        tx.commit()?;
        Ok((a, true))
    }
    pub(crate) fn settle_attachment(
        &mut self,
        sid: &str,
        id: &str,
        released: bool,
        unknown: bool,
    ) -> Result<()> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let row = tx
            .query_opt(
                "SELECT * FROM attachments WHERE id=$1 AND session_id=$2 AND owner_epoch=$3",
                &[&id, &sid, &epoch],
            )?
            .context("Attachment not owned by this worker")?;
        let a = attachment(&row)?;
        let mut e = lock(&mut tx, &a.environment_id)?;
        if a.state == AttachmentState::Released {
            if released {
                tx.commit()?;
                return Ok(());
            }
            bail!("Released attachment cannot reactivate");
        }
        if !released && !unknown && a.state == AttachmentState::Active {
            tx.commit()?;
            return Ok(());
        }
        if !released && (e.phase != Phase::Ready || unknown || a.state != AttachmentState::Pending)
        {
            bail!("Cannot activate attachment");
        }
        let state = if released { "released" } else { "active" };
        tx.execute(
            "UPDATE attachments SET state=$2,outcome_unknown=$3 WHERE id=$1",
            &[&id, &state, &unknown],
        )?;
        change(
            &mut tx,
            &mut e,
            "attachment_settled",
            json!({"attachment_id":id,"state":state,"outcome_unknown":unknown}),
        )?;
        event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "attachment_settled",
            json!({"attachment_id":id,"state":state,"outcome_unknown":unknown}),
        )?;
        tx.commit()?;
        Ok(())
    }
    pub(crate) fn abandon_attachments(&mut self, sid: &str) -> Result<()> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let Some(b) = &s.environment else {
            return Ok(());
        };
        let mut e = lock(&mut tx, &b.environment_id)?;
        let count=tx.execute("UPDATE attachments SET state='released',outcome_unknown=TRUE WHERE session_id=$1 AND owner_epoch<$2 AND state<>'released'",&[&sid,&epoch])?;
        if count > 0 {
            change(
                &mut tx,
                &mut e,
                "attachments_abandoned",
                json!({"session_id":sid,"count":count,"outcome_unknown":true}),
            )?;
            event(
                &mut tx,
                &s,
                &self.owner,
                epoch,
                "attachments_abandoned",
                json!({"environment_id":e.id,"count":count,"outcome_unknown":true}),
            )?;
        }
        tx.commit()?;
        Ok(())
    }
}
