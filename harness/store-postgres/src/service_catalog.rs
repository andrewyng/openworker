//! Immutable service definitions and durable management intents. Provider I/O is
//! performed by the controller only after the intent commits.
use crate::{
    auth::{self, Action, Identity, Scope},
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use openworker_runtime::{
    environment::{Phase, Record, Sharing, Spec},
    store::Session,
};
use postgres::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Profile {
    pub id: String,
    pub provider: String,
    pub cpu: u16,
    #[serde(rename = "memoryMB")]
    pub memory_mb: u32,
    pub sharing: Sharing,
    #[serde(default)]
    pub approval_mode: openworker_runtime::permissions::Mode,
    #[serde(default)]
    pub auto_skip_questions: bool,
    #[serde(default)]
    pub allowed_principals: Vec<String>,
}
impl Profile {
    pub fn validate(&self) -> Result<()> {
        revision(&self.id)?;
        ensure!(
            self.provider == "openshell",
            SubmissionError::InvalidRequest
        );
        ensure!(
            (1..=16).contains(&self.cpu) && (256..=65536).contains(&self.memory_mb),
            SubmissionError::InvalidRequest
        );
        principals(&self.allowed_principals)
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Release {
    pub id: String,
    pub instructions: String,
    pub tools: Vec<String>,
    pub runtime_profiles: Vec<String>,
    #[serde(default)]
    pub default_runtime_profile: Option<String>,
    #[serde(default)]
    pub allowed_principals: Vec<String>,
    #[serde(default)]
    pub default_model: Option<openworker_runtime::connections::Choice>,
    #[serde(default)]
    pub reviewer_model: Option<openworker_runtime::connections::Choice>,
    #[serde(default)]
    pub model_overrides: Vec<openworker_runtime::connections::Choice>,
}
impl Release {
    pub fn validate(&self) -> Result<()> {
        revision(&self.id)?;
        ensure!(
            !self.instructions.trim().is_empty()
                && self.instructions.len() <= 32000
                && !self.tools.is_empty()
                && self.tools.len() <= 64
                && !self.runtime_profiles.is_empty()
                && self.runtime_profiles.len() <= 16
                && self
                    .default_runtime_profile
                    .as_ref()
                    .is_none_or(|p| self.runtime_profiles.contains(p)),
            SubmissionError::InvalidRequest
        );
        for id in &self.runtime_profiles {
            revision(id)?;
        }
        for model in self
            .default_model
            .iter()
            .chain(self.reviewer_model.iter())
            .chain(&self.model_overrides)
        {
            model.validate()?;
        }
        ensure!(
            self.model_overrides.len() <= 32,
            SubmissionError::InvalidRequest
        );
        principals(&self.allowed_principals)
    }
}
fn principals(p: &[String]) -> Result<()> {
    ensure!(p.len() <= 100, SubmissionError::InvalidRequest);
    for s in p {
        crate::submissions::validate_id(s, 128)?;
    }
    Ok(())
}
fn revision(s: &str) -> Result<()> {
    name(s)?;
    let (id, version) = s.split_once('@').ok_or(SubmissionError::InvalidRequest)?;
    ensure!(
        !id.is_empty() && !version.is_empty() && !version.contains('@'),
        SubmissionError::InvalidRequest
    );
    Ok(())
}
pub fn name(s: &str) -> Result<()> {
    ensure!(
        !s.is_empty()
            && s.len() <= 80
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-@.".contains(&b))
            && s != "."
            && s != "..",
        SubmissionError::InvalidRequest
    );
    Ok(())
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Entry {
    pub id: String,
    pub kind: String,
    pub digest: String,
    pub definition: Value,
    pub installation: String,
    pub activated: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Admission {
    pub agent: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub initial_message: Option<String>,
    #[serde(default)]
    pub model: Option<openworker_runtime::connections::Choice>,
    #[serde(default)]
    pub runtime_profile: Option<String>,
    #[serde(default)]
    pub environment_id: Option<String>,
    #[serde(default = "default_project")]
    pub project: String,
    #[serde(default)]
    pub name: Option<String>,
}
fn default_project() -> String {
    "default".into()
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Operation {
    pub id: String,
    #[serde(default)]
    pub run_id: Option<String>,
    #[serde(default)]
    pub submission_id: Option<String>,
    pub session_id: String,
    pub environment_id: String,
    pub release_id: String,
    pub profile_id: String,
    pub principal: String,
    pub project: String,
    pub status: String,
    pub receipt: Option<Value>,
    pub error: Option<String>,
}
fn op(row: &postgres::Row) -> Operation {
    let id: String = row.get("id");
    let task = row.get::<_, Value>("input")["initialMessage"].is_string();
    Operation {
        run_id: task.then(|| format!("t_{}", id.trim_start_matches("op_"))),
        submission_id: task.then(|| id.clone()),
        id,
        session_id: row.get("session_id"),
        environment_id: row.get("environment_id"),
        release_id: row.get("release_id"),
        profile_id: row.get("profile_id"),
        principal: row.get("principal"),
        project: row.get("project"),
        status: row.get("status"),
        receipt: row.get("receipt"),
        error: row.get("error"),
    }
}
fn entry(tx: &mut Transaction<'_>, kind: &str, id: &str) -> Result<Entry> {
    let r = tx
        .query_opt(
            "SELECT data,activated FROM service_catalog WHERE kind=$1 AND id=$2 FOR SHARE",
            &[&kind, &id],
        )?
        .ok_or(SubmissionError::NotFound)?;
    let mut e: Entry = serde_json::from_value(r.get(0))?;
    e.activated = r.get(1);
    Ok(e)
}
fn allowed(p: &Identity, list: &[String]) -> Result<()> {
    ensure!(
        p.role.permits(Action::Admin) || list.contains(&p.principal),
        auth::Error::Forbidden
    );
    Ok(())
}
fn resolve(tx: &mut Transaction<'_>, kind: &str, id: &str) -> Result<Entry> {
    let resolved: Option<String> = tx
        .query_opt(
            "SELECT target FROM service_aliases WHERE kind=$1 AND alias=$2",
            &[&kind, &id],
        )?
        .map(|r| r.get(0));
    entry(tx, kind, resolved.as_deref().unwrap_or(id))
}
pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    crate::service_admin::migrate(tx)?;
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_catalog(kind TEXT NOT NULL,id TEXT NOT NULL,data JSONB NOT NULL,activated BOOLEAN NOT NULL DEFAULT FALSE,PRIMARY KEY(kind,id));
    CREATE TABLE IF NOT EXISTS service_aliases(kind TEXT NOT NULL,alias TEXT NOT NULL,target TEXT NOT NULL,PRIMARY KEY(kind,alias));
    CREATE TABLE IF NOT EXISTS service_management_receipts(id TEXT PRIMARY KEY,payload JSONB NOT NULL,result JSONB NOT NULL);
    CREATE TABLE IF NOT EXISTS service_environment_owners(environment_id TEXT PRIMARY KEY REFERENCES environments(id),profile_id TEXT NOT NULL,principal TEXT NOT NULL,project TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS service_deletions(environment_id TEXT PRIMARY KEY REFERENCES environments(id),request_key TEXT NOT NULL,dispatched BOOLEAN NOT NULL DEFAULT FALSE,next_check TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
    CREATE TABLE IF NOT EXISTS service_operations(id TEXT PRIMARY KEY,request_key TEXT NOT NULL UNIQUE,input JSONB NOT NULL,principal TEXT NOT NULL,credential_id TEXT NOT NULL,release_id TEXT NOT NULL,profile_id TEXT NOT NULL,environment_id TEXT NOT NULL REFERENCES environments(id),session_id TEXT NOT NULL UNIQUE,project TEXT NOT NULL,status TEXT NOT NULL,receipt JSONB,error TEXT,next_check TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());")?;
    tx.batch_execute(
        "ALTER TABLE service_operations ADD COLUMN IF NOT EXISTS configuration JSONB",
    )?;
    tx.execute(
        "UPDATE service_operations SET configuration=$1 WHERE configuration IS NULL",
        &[&json!(crate::service_admin::Revision {
            revision: 1,
            configuration: Default::default()
        })],
    )?;
    tx.batch_execute("ALTER TABLE service_operations ALTER COLUMN configuration SET NOT NULL")?;
    Ok(())
}
impl PostgresStore {
    pub fn catalog_put(
        &self,
        request: &str,
        kind: &str,
        definition: Value,
        installation: &str,
    ) -> Result<Entry> {
        ensure!(
            matches!(kind, "profile" | "release"),
            SubmissionError::InvalidRequest
        );
        let id = definition["id"]
            .as_str()
            .ok_or(SubmissionError::InvalidRequest)?
            .to_owned();
        name(&id)?;
        if kind == "profile" {
            serde_json::from_value::<Profile>(definition.clone())?.validate()?;
        } else {
            serde_json::from_value::<Release>(definition.clone())?.validate()?;
        }
        let (request, kind, installation) =
            (request.to_owned(), kind.to_owned(), installation.to_owned());
        self.call(move |db| db.catalog_put(request, kind, id, definition, installation))
    }
    pub fn catalog_activate(
        &self,
        request: &str,
        kind: &str,
        id: &str,
        alias: Option<String>,
    ) -> Result<Entry> {
        name(id)?;
        if let Some(a) = &alias {
            name(a)?;
            ensure!(!a.contains('@'), SubmissionError::InvalidRequest);
        }
        let (request, kind, id) = (request.to_owned(), kind.to_owned(), id.to_owned());
        self.call(move |db| db.catalog_activate(request, kind, id, alias))
    }
    pub fn catalog_get(&self, kind: &str, id: &str) -> Result<Entry> {
        let (kind, id) = (kind.to_owned(), id.to_owned());
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = auth::guard(&mut tx, db.auth.as_ref(), Action::Read, None, None)?;
            let e = resolve(&mut tx, &kind, &id)?;
            if let Some(p) = p {
                let principals: Vec<String> =
                    serde_json::from_value(e.definition["allowedPrincipals"].clone())
                        .unwrap_or_default();
                // Deployment authority can inspect definitions to validate references;
                // execution admission still enforces the principal allowlist separately.
                let manages = p.role.permits(Action::Publish)
                    || p.role.permits(Action::Activate)
                    || p.role.permits(Action::Configure)
                    || p.role.permits(Action::Admin);
                if !manages {
                    allowed(&p, &principals)?;
                }
                ensure!(
                    e.activated
                        || p.role.permits(Action::Publish)
                        || p.role.permits(Action::Activate)
                        || p.role.permits(Action::Admin),
                    auth::Error::Forbidden
                );
            }
            tx.commit()?;
            Ok(e)
        })
    }
    /// Bounded public catalog projection. Never expose drafts or principal-restricted
    /// definitions to callers who could not read the same entry individually.
    pub fn catalog_visible(&self, kind: &str) -> Result<Vec<Entry>> {
        ensure!(
            matches!(kind, "profile" | "release"),
            SubmissionError::InvalidRequest
        );
        let kind = kind.to_owned();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = auth::guard(&mut tx, db.auth.as_ref(), Action::Read, None, None)?;
            let mut out = vec![];
            // Catalog insertion already enforces a 64-entry bound per kind.
            for row in tx.query(
                "SELECT data,activated FROM service_catalog WHERE kind=$1 ORDER BY id LIMIT 64",
                &[&kind],
            )? {
                let mut e: Entry = serde_json::from_value(row.get(0))?;
                e.activated = row.get(1);
                if let Some(p) = &p {
                    let manages = p.role.permits(Action::Publish)
                        || p.role.permits(Action::Activate)
                        || p.role.permits(Action::Configure)
                        || p.role.permits(Action::Admin);
                    let principals: Vec<String> =
                        serde_json::from_value(e.definition["allowedPrincipals"].clone())
                            .unwrap_or_default();
                    if !manages && allowed(p, &principals).is_err() {
                        continue;
                    }
                    if !e.activated
                        && !(p.role.permits(Action::Publish)
                            || p.role.permits(Action::Activate)
                            || p.role.permits(Action::Admin))
                    {
                        continue;
                    }
                }
                out.push(e);
            }
            tx.commit()?;
            Ok(out)
        })
    }
    pub fn catalog_list(&self) -> Result<Vec<Entry>> {
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            auth::guard(&mut tx, db.auth.as_ref(), Action::Admin, None, None)?;
            let mut out = vec![];
            for r in tx.query(
                "SELECT data,activated FROM service_catalog ORDER BY kind,id",
                &[],
            )? {
                let mut e: Entry = serde_json::from_value(r.get(0))?;
                e.activated = r.get(1);
                out.push(e);
            }
            tx.commit()?;
            Ok(out)
        })
    }
    pub fn service_admit(
        &self,
        key: &str,
        input: Admission,
        release: Entry,
        profile: Entry,
        spec: Spec,
    ) -> Result<Operation> {
        name(&input.project)?;
        ensure!(
            !(input.runtime_profile.is_some() && input.environment_id.is_some()),
            SubmissionError::InvalidRequest
        );
        spec.validate()?;
        if let Some(text) = &input.initial_message {
            crate::submissions::validate_id(text, 32000)?;
        }
        let key = key.to_owned();
        self.call(move |db| db.service_admit(key, input, release, profile, spec))
    }
    pub fn service_session_identity(&self, sid: &str) -> Result<Option<Value>> {
        let sid = sid.to_owned();
        self.call(move |db| db.service_session_identity(sid))
    }
    pub fn service_operation(&self, id: &str) -> Result<Operation> {
        let id = id.to_owned();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = auth::guard(&mut tx, db.auth.as_ref(), Action::Read, None, None)?;
            let r = tx
                .query_opt("SELECT * FROM service_operations WHERE id=$1", &[&id])?
                .ok_or(SubmissionError::NotFound)?;
            let o = op(&r);
            if let Some(p) = p {
                ensure!(
                    p.principal == o.principal || p.role.permits(Action::Admin),
                    auth::Error::Forbidden
                );
            }
            tx.commit()?;
            Ok(o)
        })
    }
    pub fn service_environment(&self, id: &str) -> Result<Value> {
        let id = id.to_owned();
        self.call(move |db| db.service_environment(id))
    }
    /// Resume-safe enqueue: begin_environment_delete already fenced attachments.
    pub fn service_queue_delete(&self, id: &str, key: &str) -> Result<()> {
        let (id, key) = (id.to_owned(), key.to_owned());
        self.call(move|db| {let mut tx=db.client.transaction()?;
            auth::guard(&mut tx,db.auth.as_ref(),auth::Action::Admin,None,None)?;
            let recorded:Option<String>=tx.query_one("SELECT delete_request_id FROM environments WHERE id=$1",&[&id])?.get(0);
            ensure!(recorded.as_deref()==Some(&key),"Deletion identity mismatch");
            tx.execute("INSERT INTO service_deletions(environment_id,request_key) VALUES($1,$2) ON CONFLICT DO NOTHING",&[&id,&key])?;tx.commit()?;Ok(())})
    }
    /// One dispatch authorization. Later claims only inspect the original remote object.
    pub fn service_claim_delete(
        &self,
        installed: &str,
    ) -> Result<
        Option<(
            openworker_runtime::environment::Record,
            String,
            String,
            bool,
        )>,
    > {
        ensure!(self.auth.is_none(), "Controller requires private store");
        let installed = installed.to_owned();
        self.call(move|db|{let mut tx=db.client.transaction()?;
            let row=tx.query_opt("SELECT d.environment_id,d.request_key,d.dispatched,o.profile_id,e.data FROM service_deletions d JOIN environments e ON e.id=d.environment_id JOIN service_environment_owners o ON o.environment_id=d.environment_id WHERE e.data->>'phase'='deleting' AND d.next_check<=clock_timestamp() AND EXISTS(SELECT 1 FROM service_operations a JOIN service_catalog c ON c.kind='release' AND c.id=a.release_id WHERE a.environment_id=d.environment_id AND c.data->>'installation'=$1) ORDER BY d.next_check,d.environment_id LIMIT 1 FOR UPDATE OF d SKIP LOCKED",&[&installed])?;
            let Some(row)=row else {return Ok(None)};
            let id:String=row.get(0);let key:String=row.get(1);let dispatched:bool=row.get(2);let profile:String=row.get(3);let record=serde_json::from_value(row.get(4))?;
            tx.execute("UPDATE service_deletions SET dispatched=TRUE,next_check=clock_timestamp()+interval '180 seconds' WHERE environment_id=$1",&[&id])?;
            tx.commit()?;Ok(Some((record,key,profile,!dispatched)))})
    }
    pub fn service_recheck_delete(&self, id: &str) -> Result<()> {
        ensure!(self.auth.is_none(), "Controller requires private store");
        let id = id.to_owned();
        self.call(move|db|{db.client.execute("UPDATE service_deletions SET next_check=clock_timestamp()+interval '2 seconds' WHERE environment_id=$1",&[&id])?;Ok(())})
    }
    /// Controller-only claim: a reclaimed dispatch only reconciles, never creates again.
    pub fn service_claim(&self) -> Result<Option<(Operation, bool)>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        self.service_claim_for(None)
    }
    pub fn service_claim_for(
        &self,
        installation: Option<&str>,
    ) -> Result<Option<(Operation, bool)>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let installation = installation.map(String::from);
        self.call(move |db| db.service_claim(installation))
    }
    pub fn service_update_operation(
        &self,
        id: &str,
        status: &str,
        error: Option<String>,
    ) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        ensure!(
            matches!(status, "unknown" | "ready" | "failed"),
            SubmissionError::InvalidRequest
        );
        let (id, status) = (id.to_owned(), status.to_owned());
        self.call(move |db| {
            db.client.execute("UPDATE service_operations SET status=$2,error=$3,next_check=clock_timestamp()+interval '2 seconds' WHERE id=$1 AND status<>'complete'",&[&id,&status,&error])?;
            Ok(())}
        )
    }
    pub fn service_input(&self, id: &str) -> Result<Admission> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let id = id.to_owned();
        self.call(move |db| {
            Ok(serde_json::from_value(
                db.client
                    .query_one("SELECT input FROM service_operations WHERE id=$1", &[&id])?
                    .get(0),
            )?)
        })
    }
    pub fn service_finish(
        &self,
        id: &str,
        session: Session,
        service_id: &str,
        namespace: &str,
    ) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let (id, service_id, namespace) =
            (id.to_owned(), service_id.to_owned(), namespace.to_owned());
        self.call(move |db| db.service_finish(id, session, service_id, namespace))
    }
}

impl crate::db::Database {
    fn catalog_put(
        &mut self,
        request: String,
        kind: String,
        id: String,
        definition: Value,
        installation: String,
    ) -> Result<Entry> {
        let mut tx = self.client.transaction()?;
        let action = if kind == "release" {
            Action::Publish
        } else {
            Action::Configure
        };
        let p = auth::guard(&mut tx, self.auth.as_ref(), action, None, None)?;
        tx.query_one(
            "SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('service-catalog'))",
            &[],
        )?;
        let digest = openworker_runtime::bundle::digest(&serde_json::to_vec(&json!([
            &definition,
            &installation
        ]))?);
        let e = Entry {
            id: id.clone(),
            kind: kind.clone(),
            digest,
            definition,
            installation,
            activated: false,
        };
        let payload = json!({
            "put":e}
        );
        if let Some(r) = tx.query_opt(
            "SELECT payload,result FROM service_management_receipts WHERE id=$1",
            &[&request],
        )? {
            ensure!(r.get::<_, Value>(0) == payload, SubmissionError::Conflict);
            return Ok(serde_json::from_value(r.get(1))?);
        }
        if let Some(r) = tx.query_opt(
            "SELECT data FROM service_catalog WHERE kind=$1 AND id=$2",
            &[&kind, &id],
        )? {
            let old: Entry = serde_json::from_value(r.get(0))?;
            ensure!(old.digest == e.digest, SubmissionError::Conflict);
        }
        let count: i64 = tx
            .query_one("SELECT count(*) FROM service_catalog", &[])?
            .get(0);
        ensure!(count < 64, SubmissionError::QueueFull);
        tx.execute(
            "INSERT INTO service_catalog(kind,id,data) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            &[&kind, &id, &json!(e)],
        )?;
        tx.execute(
            "INSERT INTO service_management_receipts VALUES($1,$2,$3)",
            &[&request, &payload, &json!(e)],
        )?;
        auth::audit_target(&mut tx, p.as_ref(), action, &request, &e.id)?;
        tx.commit()?;
        Ok(e)
    }
    fn catalog_activate(
        &mut self,
        request: String,
        kind: String,
        id: String,
        alias: Option<String>,
    ) -> Result<Entry> {
        let mut tx = self.client.transaction()?;
        let action = if kind == "release" {
            Action::Activate
        } else {
            Action::Configure
        };
        let p = auth::guard(&mut tx, self.auth.as_ref(), action, None, None)?;
        tx.query_one(
            "SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('service-catalog'))",
            &[],
        )?;
        let payload = json!({
            "activate":id,"kind":kind,"alias":alias}
        );
        if let Some(r) = tx.query_opt(
            "SELECT payload,result FROM service_management_receipts WHERE id=$1",
            &[&request],
        )? {
            ensure!(r.get::<_, Value>(0) == payload, SubmissionError::Conflict);
            return Ok(serde_json::from_value(r.get(1))?);
        }
        let mut e = entry(&mut tx, &kind, &id)?;
        e.activated = true;
        if kind == "release" {
            let r: Release = serde_json::from_value(e.definition.clone())?;
            for profile in r.runtime_profiles {
                let profile = entry(&mut tx, "profile", &profile)?;
                ensure!(
                    profile.activated && profile.installation == e.installation,
                    SubmissionError::InvalidRequest
                );
            }
        }
        tx.execute(
            "UPDATE service_catalog SET activated=TRUE WHERE kind=$1 AND id=$2",
            &[&kind, &id],
        )?;
        if let Some(alias) = alias {
            ensure!(
                tx.query_opt(
                    "SELECT 1 FROM service_catalog WHERE kind=$1 AND id=$2",
                    &[&kind, &alias]
                )?
                .is_none(),
                SubmissionError::Conflict
            );
            tx.execute("INSERT INTO service_aliases VALUES($1,$2,$3) ON CONFLICT(kind,alias) DO UPDATE SET target=EXCLUDED.target",&[&kind,&alias,&id])?;
        }
        tx.execute(
            "INSERT INTO service_management_receipts VALUES($1,$2,$3)",
            &[&request, &payload, &json!(e)],
        )?;
        auth::audit_target(&mut tx, p.as_ref(), action, &request, &e.id)?;
        tx.commit()?;
        Ok(e)
    }
    fn service_admit(
        &mut self,
        key: String,
        input: Admission,
        release: Entry,
        profile: Entry,
        spec: Spec,
    ) -> Result<Operation> {
        let mut tx = self.client.transaction()?;
        let p = auth::guard(&mut tx, self.auth.as_ref(), Action::Submit, None, None)?
            .ok_or(auth::Error::Unauthorized)?;
        ensure!(
            !matches!(p.scope, Scope::Sessions { .. }),
            auth::Error::Forbidden
        );
        tx.query_one(
            "SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext($1))",
            &[&format!("service-admit:{key}")],
        )?;
        if let Some(row) = tx.query_opt(
            "SELECT * FROM service_operations WHERE request_key=$1",
            &[&key],
        )? {
            ensure!(
                row.get::<_, Value>("input") == json!(input)
                    && row.get::<_, String>("principal") == p.principal,
                SubmissionError::Conflict
            );
            let o = op(&row);
            tx.commit()?;
            return Ok(o);
        }
        let configured = crate::service_admin::current(&mut tx)?;
        ensure!(
            !configured.configuration.admissions_paused,
            SubmissionError::Busy
        );
        let r = entry(&mut tx, "release", &release.id)?;
        let f = entry(&mut tx, "profile", &profile.id)?;
        ensure!(
            r.digest == release.digest && f.digest == profile.digest && r.activated && f.activated,
            SubmissionError::Conflict
        );
        let rel: Release = serde_json::from_value(r.definition)?;
        let prof: Profile = serde_json::from_value(f.definition)?;
        allowed(&p, &rel.allowed_principals)?;
        allowed(&p, &prof.allowed_principals)?;
        ensure!(
            rel.runtime_profiles.contains(&prof.id),
            auth::Error::Forbidden
        );
        if let Some(model) = &input.model {
            ensure!(
                rel.model_overrides.contains(model) || rel.default_model.as_ref() == Some(model),
                auth::Error::Forbidden
            );
        }
        ensure!(
            prof.approval_mode != openworker_runtime::permissions::Mode::AutoApprove
                || rel.reviewer_model.is_some(),
            SubmissionError::InvalidRequest
        );
        tx.query_one("SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('service-environment-quota'))",&[])?;
        let pending: i64 = tx
            .query_one(
                "SELECT count(*) FROM service_operations WHERE status NOT IN ('complete','failed')",
                &[],
            )?
            .get(0);
        ensure!(
            pending < configured.configuration.max_pending_operations,
            SubmissionError::QueueFull
        );
        if input.environment_id.is_none() {
            let owned:i64=tx.query_one("SELECT count(*) FROM service_environment_owners o JOIN environments e ON e.id=o.environment_id WHERE o.principal=$1 AND e.data->>'phase'<>'deleted'",&[&p.principal])?.get(0);
            let total:i64=tx.query_one("SELECT count(*) FROM service_environment_owners o JOIN environments e ON e.id=o.environment_id WHERE e.data->>'phase'<>'deleted'",&[])?.get(0);
            ensure!(
                owned < configured.configuration.max_environments_per_principal
                    && total < configured.configuration.max_environments,
                SubmissionError::QueueFull
            );
        }
        let operation_id = openworker_runtime::id("op");
        let session_id = openworker_runtime::id("s");
        let (environment_id, status) = if let Some(eid) = &input.environment_id {
            let owner=tx.query_opt("SELECT profile_id,principal,project FROM service_environment_owners WHERE environment_id=$1",&[eid])?.ok_or(auth::Error::Forbidden)?;
            ensure!(
                (p.role.permits(Action::Admin) || owner.get::<_, String>(1) == p.principal)
                    && owner.get::<_, String>(2) == input.project
                    && owner.get::<_, String>(0) == prof.id,
                auth::Error::Forbidden
            );
            let row = tx
                .query_opt(
                    "SELECT data FROM environments WHERE id=$1 FOR UPDATE",
                    &[eid],
                )?
                .ok_or(SubmissionError::NotFound)?;
            let record: Record = serde_json::from_value(row.get(0))?;
            ensure!(
                record.phase == Phase::Ready && record.spec == spec,
                SubmissionError::Conflict
            );
            ensure!(
                record.spec.sharing == Sharing::Shared,
                SubmissionError::Conflict
            );
            (eid.clone(), "ready")
        } else {
            let record = Record {
                id: openworker_runtime::id("env"),
                request_id: operation_id.clone(),
                actor: p.principal.clone(),
                spec,
                phase: Phase::Provisioning,
                binding: None,
                revision: 1,
            };
            tx.execute(
                "INSERT INTO environments(id,request_id,data) VALUES($1,$2,$3)",
                &[&record.id, &record.request_id, &json!(record)],
            )?;
            tx.execute("INSERT INTO environment_events(environment_id,revision,kind,data) VALUES($1,1,'environment_requested',$2)",&[&record.id,&json!({
                    "operationId":operation_id,"actor":p.principal}
                )])?;
            tx.execute(
                "INSERT INTO service_environment_owners VALUES($1,$2,$3,$4)",
                &[&record.id, &prof.id, &p.principal, &input.project],
            )?;
            (record.id, "queued")
        };
        let row=tx.query_one("INSERT INTO service_operations(id,request_key,input,principal,credential_id,release_id,profile_id,environment_id,session_id,project,status,configuration) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING *",&[&operation_id,&key,&json!(input),&p.principal,&p.credential_id,&rel.id,&prof.id,&environment_id,&session_id,&input.project,&status,&json!(configured)])?;
        auth::audit(&mut tx, Some(&p), Action::Create, None, Some(&key))?;
        let result = op(&row);
        tx.commit()?;
        Ok(result)
    }
    fn service_environment(&mut self, id: String) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        let p = auth::guard(&mut tx, self.auth.as_ref(), Action::Read, None, None)?;
        let r=tx.query_opt("SELECT profile_id,principal,project FROM service_environment_owners WHERE environment_id=$1",&[&id])?.ok_or(SubmissionError::NotFound)?;
        let owner: String = r.get(1);
        if let Some(p) = p {
            ensure!(
                p.principal == owner || p.role.permits(Action::Admin),
                auth::Error::Forbidden
            );
        }
        let e = tx
            .query_one("SELECT data FROM environments WHERE id=$1", &[&id])?
            .get::<_, Value>(0);
        let attachments:Vec<Value>=tx.query("SELECT session_id,state FROM attachments WHERE environment_id=$1 AND state<>'released'",&[&id])?.into_iter().map(|r|json!({
                "sessionId":r.get::<_,String>(0),"state":r.get::<_,String>(1)}
            )).collect();
        tx.commit()?;
        Ok(json!({
            "id":id,"runtimeProfile":r.get::<_,String>(0),"principal":owner,"project":r.get::<_,String>(2),"environment":e,"attachments":attachments}
        ))
    }
    fn service_claim(&mut self, installation: Option<String>) -> Result<Option<(Operation, bool)>> {
        let mut tx = self.client.transaction()?;
        let row=tx.query_opt("SELECT * FROM service_operations WHERE status IN ('queued','dispatching','unknown','ready') AND next_check<=clock_timestamp() AND ($1::text IS NULL OR EXISTS(SELECT 1 FROM service_catalog c WHERE c.kind='release' AND c.id=service_operations.release_id AND c.data->>'installation'=$1)) ORDER BY next_check,id LIMIT 1 FOR UPDATE SKIP LOCKED",&[&installation])?;
        let Some(r) = row else { return Ok(None) };
        let mut o = op(&r);
        let create = o.status == "queued";
        if create {
            o.status = "dispatching".into();
        }
        tx.execute("UPDATE service_operations SET status=$2,next_check=clock_timestamp()+interval '180 seconds' WHERE id=$1",&[&o.id,&o.status])?;
        tx.commit()?;
        Ok(Some((o, create)))
    }
    fn service_session_identity(&mut self, sid: String) -> Result<Option<Value>> {
        let mut tx = self.client.transaction()?;
        auth::guard(&mut tx, self.auth.as_ref(), Action::Read, Some(&sid), None)?;
        let result=tx.query_opt("SELECT release_id,profile_id,project,id,configuration FROM service_operations WHERE session_id=$1 AND status='complete'",&[&sid])?.map(|r|json!({"releaseId":r.get::<_,String>(0),"runtimeProfile":r.get::<_,String>(1),"project":r.get::<_,String>(2),"operationId":r.get::<_,String>(3),"configuration":r.get::<_,Value>(4)}));
        tx.commit()?;
        Ok(result)
    }
    fn service_finish(
        &mut self,
        id: String,
        mut session: Session,
        service_id: String,
        namespace: String,
    ) -> Result<()> {
        let mut tx = self.client.transaction()?;
        let row = tx.query_one(
            "SELECT * FROM service_operations WHERE id=$1 FOR UPDATE",
            &[&id],
        )?;
        let o = op(&row);
        if o.status == "complete" {
            return Ok(());
        }
        ensure!(o.status == "ready", SubmissionError::Conflict);
        let credential: String = row.get("credential_id");
        let cred = tx
            .query_opt(
                "SELECT spec FROM api_credentials WHERE id=$1 AND NOT revoked AND ((spec->>'expires_at') IS NULL OR (spec->>'expires_at')::bigint>extract(epoch FROM clock_timestamp())) FOR SHARE",
                &[&credential],
            )?
            .ok_or(auth::Error::Unauthorized)?;
        let spec: auth::Spec = serde_json::from_value(cred.get(0))?;
        let p = Identity {
            credential_id: credential,
            principal: spec.principal,
            role: spec.role,
            scope: spec.scope,
            environments: spec.environments,
            expires_at: spec.expires_at,
        };
        ensure!(
            p.principal == o.principal && p.role.permits(Action::Submit),
            auth::Error::Forbidden
        );
        let rel: Release =
            serde_json::from_value(entry(&mut tx, "release", &o.release_id)?.definition)?;
        let profile: Profile =
            serde_json::from_value(entry(&mut tx, "profile", &o.profile_id)?.definition)?;
        allowed(&p, &rel.allowed_principals)?;
        allowed(&p, &profile.allowed_principals)?;
        session.id = o.session_id.clone();
        let input: Admission = serde_json::from_value(row.get("input"))?;
        let req = crate::SessionCreation {
            request_id: o.id.clone(),
            actor: o.principal.clone(),
            parameters: json!({
                "serviceOperation":o.id,"environment_id":o.environment_id,"input":input}
            ),
        };
        crate::ingress::create_in_tx(&mut tx, &self.owner, req, session, Some(p.clone()))?;
        // The session, first task and completed operation receipt commit together.
        // No worker can observe an idle half-submission or execute a second copy.
        if let Some(text) = input.initial_message {
            let request = crate::TurnSubmission {
                session_id: o.session_id.clone(),
                submission_id: o.submission_id.clone().expect("initial task identity"),
                text,
            };
            let receipt = crate::submissions::insert_reserved(
                &mut tx,
                &request,
                "queued",
                None,
                o.run_id.as_deref().expect("initial run identity"),
            )?;
            crate::submissions::journal_acceptance(&mut tx, &receipt, Some(&p.principal))?;
            auth::audit(
                &mut tx,
                Some(&p),
                Action::Submit,
                Some(&o.session_id),
                Some(&request.submission_id),
            )?;
        }
        let receipt = json!({
            "serviceId":service_id,"namespace":namespace,"sessionId":o.session_id,"agent":o.release_id,"releaseId":o.release_id,"runtimeProfile":o.profile_id,"environmentId":o.environment_id,"operationId":o.id,"runId":o.run_id,"submissionId":o.submission_id}
        );
        tx.execute(
            "UPDATE service_operations SET status='complete',receipt=$2,error=NULL WHERE id=$1",
            &[&id, &receipt],
        )?;
        tx.commit()?;
        Ok(())
    }
}
