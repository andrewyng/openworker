//! API credentials are separate from agent/tool permissions. Trusted embedding
//! calls may omit Context; HTTP must use an authorized handle on every route.
use crate::{db::Database, PostgresStore};
use anyhow::{ensure, Result};
use postgres::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    Unauthorized,
    Forbidden,
    Conflict,
    Unconfigured,
    Invalid,
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Unauthorized => "unauthorized",
            Self::Forbidden => "forbidden",
            Self::Conflict => "credential_conflict",
            Self::Unconfigured => "authorization_unconfigured",
            Self::Invalid => "invalid_credential_request",
        })
    }
}
impl std::error::Error for Error {}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    Observer,
    Operator,
    Administrator,
    Publisher,
    ReleaseAdministrator,
    ServiceAdministrator,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Scope {
    All,
    Owned,
    Sessions { ids: Vec<String> },
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spec {
    pub principal: String,
    pub role: Role,
    pub scope: Scope,
    #[serde(default)]
    pub environments: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<i64>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Identity {
    pub credential_id: String,
    pub principal: String,
    pub role: Role,
    pub scope: Scope,
    pub environments: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<i64>,
}
#[derive(Clone)]
pub(crate) struct Context {
    digest: String,
}
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Read,
    Create,
    Submit,
    Control,
    Artifact,
    Admin,
    Publish,
    Activate,
    Configure,
}
impl Action {
    fn name(self) -> &'static str {
        match self {
            Self::Read => "read",
            Self::Create => "session.create",
            Self::Submit => "turn.submit",
            Self::Control => "control.submit",
            Self::Artifact => "artifact.write",
            Self::Admin => "credential.manage",
            Self::Publish => "release.publish",
            Self::Activate => "release.activate",
            Self::Configure => "service.configure",
        }
    }
}
fn id(s: &str, max: usize) -> Result<()> {
    openworker_artifacts::component(s, max).map_err(|_| Error::Invalid.into())
}
fn token_hash(token: &str) -> Result<String> {
    ensure!(
        (32..=256).contains(&token.len())
            && token
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b)),
        Error::Invalid
    );
    Ok(openworker_artifacts::hash(
        format!("openworker-api-key-v1:{token}").as_bytes(),
    ))
}
impl Role {
    pub fn permits(self, action: Action) -> bool {
        match action {
            Action::Read => true,
            Action::Create | Action::Submit | Action::Control | Action::Artifact => {
                matches!(self, Self::Operator | Self::Administrator)
            }
            Action::Admin => matches!(self, Self::Administrator | Self::ServiceAdministrator),
            Action::Publish => matches!(self, Self::Administrator | Self::Publisher),
            Action::Activate => matches!(self, Self::Administrator | Self::ReleaseAdministrator),
            Action::Configure => matches!(
                self,
                Self::Administrator | Self::ServiceAdministrator | Self::ReleaseAdministrator
            ),
        }
    }
}
impl Spec {
    pub fn validate(&self) -> Result<()> {
        id(&self.principal, 64)?;
        ensure!(
            self.expires_at.is_none_or(|t| t > 0 && t < 253402300800),
            Error::Invalid
        );
        ensure!(self.environments.len() <= 128, Error::Invalid);
        for e in &self.environments {
            id(e, 80)?;
        }
        if let Scope::Sessions { ids } = &self.scope {
            ensure!(!ids.is_empty() && ids.len() <= 256, Error::Invalid);
            for sid in ids {
                id(sid, 80)?;
            }
        }
        if matches!(
            self.role,
            Role::Administrator
                | Role::Publisher
                | Role::ReleaseAdministrator
                | Role::ServiceAdministrator
        ) {
            ensure!(
                self.scope == Scope::All && self.environments.is_empty(),
                Error::Invalid
            );
        }
        if self.role == Role::Observer {
            ensure!(self.environments.is_empty(), Error::Invalid);
        }
        Ok(())
    }
}
pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    let exists: bool = tx
        .query_one(
            "SELECT to_regclass('authorization_version') IS NOT NULL",
            &[],
        )?
        .get(0);
    if exists {
        check(tx)?;
    } else {
        for table in [
            "api_credentials",
            "api_security_audit",
            "api_session_owners",
        ] {
            ensure!(
                !tx.query_one("SELECT to_regclass($1) IS NOT NULL", &[&table])?
                    .get::<_, bool>(0),
                Error::Conflict
            );
        }
        tx.batch_execute("CREATE TABLE authorization_version(singleton BOOLEAN PRIMARY KEY CHECK(singleton),version INTEGER NOT NULL);INSERT INTO authorization_version VALUES(TRUE,1);
CREATE TABLE api_credentials(id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,token_hash TEXT NOT NULL UNIQUE,spec JSONB NOT NULL,created_by TEXT NOT NULL,revoked BOOLEAN NOT NULL DEFAULT FALSE,created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE TABLE api_session_owners(session_id TEXT PRIMARY KEY REFERENCES sessions(id),principal TEXT NOT NULL);
CREATE INDEX api_sessions_principal ON api_session_owners(principal,session_id);
CREATE TABLE api_security_audit(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),credential_id TEXT,principal TEXT,action TEXT NOT NULL,session_id TEXT,request_id TEXT,outcome TEXT NOT NULL,target TEXT);")?;
    }
    Ok(())
}
fn check(tx: &mut Transaction<'_>) -> Result<()> {
    ensure!(
        tx.query_one(
            "SELECT to_regclass('authorization_version') IS NOT NULL",
            &[]
        )?
        .get::<_, bool>(0),
        Error::Unconfigured
    );
    let rows = tx.query(
        "SELECT version FROM authorization_version WHERE singleton",
        &[],
    )?;
    ensure!(
        rows.len() == 1 && rows[0].get::<_, i32>(0) == 1,
        Error::Conflict
    );
    Ok(())
}
fn identity(tx: &mut Transaction<'_>, ctx: &Context) -> Result<Identity> {
    check(tx)?;
    let r = tx
        .query_opt(
            "SELECT id,spec FROM api_credentials WHERE token_hash=$1 AND NOT revoked AND ((spec->>'expires_at') IS NULL OR (spec->>'expires_at')::bigint>extract(epoch FROM clock_timestamp())) FOR SHARE",
            &[&ctx.digest],
        )?
        .ok_or(Error::Unauthorized)?;
    let s: Spec = serde_json::from_value(r.get(1))?;
    s.validate()?;
    Ok(Identity {
        credential_id: r.get(0),
        principal: s.principal,
        role: s.role,
        scope: s.scope,
        environments: s.environments,
        expires_at: s.expires_at,
    })
}
pub(crate) fn session_allowed(tx: &mut Transaction<'_>, p: &Identity, sid: &str) -> Result<bool> {
    Ok(match &p.scope {Scope::All=>true,Scope::Sessions{ids}=>ids.iter().any(|v|v==sid),Scope::Owned=>tx.query_one("SELECT EXISTS(SELECT 1 FROM api_session_owners WHERE session_id=$1 AND principal=$2)",&[&sid,&p.principal])?.get(0)})
}
pub(crate) fn guard(
    tx: &mut Transaction<'_>,
    ctx: Option<&Context>,
    action: Action,
    sid: Option<&str>,
    environment: Option<&str>,
) -> Result<Option<Identity>> {
    let Some(ctx) = ctx else { return Ok(None) };
    let p = identity(tx, ctx)?;
    ensure!(p.role.permits(action), Error::Forbidden);
    if action == Action::Create {
        ensure!(!matches!(p.scope, Scope::Sessions { .. }), Error::Forbidden);
        ensure!(
            p.role == Role::Administrator
                || environment.is_some_and(|e| p.environments.iter().any(|v| v == e)),
            Error::Forbidden
        );
    }
    if let Some(sid) = sid {
        ensure!(session_allowed(tx, &p, sid)?, Error::Forbidden);
    }
    Ok(Some(p))
}
pub(crate) fn actor(p: Option<&Identity>, claimed: &str) -> Result<()> {
    if let Some(p) = p {
        ensure!(p.principal == claimed, Error::Forbidden);
    }
    Ok(())
}
pub(crate) fn audit(
    tx: &mut Transaction<'_>,
    p: Option<&Identity>,
    action: Action,
    sid: Option<&str>,
    request: Option<&str>,
) -> Result<()> {
    if let Some(p) = p {
        record(tx, Some(p), action.name(), sid, request, "admitted", None)?;
    }
    Ok(())
}
pub(crate) fn audit_target(
    tx: &mut Transaction<'_>,
    p: Option<&Identity>,
    action: Action,
    request: &str,
    target: &str,
) -> Result<()> {
    record(
        tx,
        p,
        action.name(),
        None,
        Some(request),
        "admitted",
        Some(target),
    )
}
fn record(
    tx: &mut Transaction<'_>,
    p: Option<&Identity>,
    action: &str,
    sid: Option<&str>,
    request: Option<&str>,
    outcome: &str,
    target: Option<&str>,
) -> Result<()> {
    // Sequence allocation alone is not commit ordering: a later commit could
    // advance a reader's cursor past an earlier, still-uncommitted record.
    // Hold this small audit append lock through transaction commit.
    tx.query_one(
        "SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('security-audit-append-v1'))",
        &[],
    )?;
    tx.execute("INSERT INTO api_security_audit(credential_id,principal,action,session_id,request_id,outcome,target) VALUES($1,$2,$3,$4,$5,$6,$7)",&[&p.map(|p|p.credential_id.as_str()),&p.map(|p|p.principal.as_str()),&action,&sid,&request,&outcome,&target])?;
    Ok(())
}
fn public(row: &postgres::Row) -> Value {
    json!({"id":row.get::<_,String>("id"),"request_id":row.get::<_,String>("request_id"),"spec":row.get::<_,Value>("spec"),"revoked":row.get::<_,bool>("revoked")})
}
fn admin_lock(tx: &mut Transaction<'_>) -> Result<()> {
    tx.query_one(
        "SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('credential-admin-v1'))",
        &[],
    )?;
    Ok(())
}
impl Database {
    pub(crate) fn auth_denied(&mut self, action: Action) -> Result<()> {
        let Some(ctx) = &self.auth else { return Ok(()) };
        let mut tx = self.client.transaction()?;
        check(&mut tx)?;
        if let Some(row) = tx.query_opt(
            "SELECT id,spec FROM api_credentials WHERE token_hash=$1",
            &[&ctx.digest],
        )? {
            let s: Spec = serde_json::from_value(row.get(1))?;
            let p = Identity {
                credential_id: row.get(0),
                principal: s.principal,
                role: s.role,
                scope: s.scope,
                environments: s.environments,
                expires_at: s.expires_at,
            };
            record(&mut tx, Some(&p), action.name(), None, None, "denied", None)?;
        }
        tx.commit()?;
        Ok(())
    }
    pub(crate) fn auth_check(
        &mut self,
        action: Action,
        sid: Option<&str>,
        environment: Option<&str>,
    ) -> Result<Identity> {
        let mut tx = self.client.transaction()?;
        let p = guard(&mut tx, self.auth.as_ref(), action, sid, environment)?
            .ok_or(Error::Unauthorized)?;
        tx.commit()?;
        Ok(p)
    }
    pub(crate) fn auth_bootstrap(
        &mut self,
        digest: String,
        principal: String,
        expires_at: Option<i64>,
    ) -> Result<Value> {
        ensure!(self.auth.is_none(), Error::Forbidden);
        let mut tx = self.client.transaction()?;
        check(&mut tx)?;
        admin_lock(&mut tx)?;
        let spec = Spec {
            principal,
            role: Role::Administrator,
            scope: Scope::All,
            environments: vec![],
            expires_at,
        };
        spec.validate()?;
        let data = serde_json::to_value(&spec)?;
        if let Some(row) = tx.query_opt(
            "SELECT * FROM api_credentials WHERE request_id='bootstrap'",
            &[],
        )? {
            ensure!(
                row.get::<_, String>("token_hash") == digest
                    && row.get::<_, Value>("spec")["principal"] == spec.principal,
                Error::Conflict
            );
            let out = public(&row);
            tx.commit()?;
            return Ok(out);
        }
        ensure!(
            tx.query_one("SELECT count(*) FROM api_credentials", &[])?
                .get::<_, i64>(0)
                == 0
                && tx
                    .query_one("SELECT count(*) FROM api_security_audit", &[])?
                    .get::<_, i64>(0)
                    == 0,
            Error::Conflict
        );
        if let Some(expiry) = spec.expires_at {
            let now: f64 = tx
                .query_one("SELECT extract(epoch FROM clock_timestamp())::float8", &[])?
                .get(0);
            ensure!(
                expiry as f64 > now && expiry as f64 <= now + 31_536_000.0,
                Error::Invalid
            );
        }
        let id = format!("key_{}", uuid::Uuid::new_v4().simple());
        let row=tx.query_one("INSERT INTO api_credentials(id,request_id,token_hash,spec,created_by) VALUES($1,'bootstrap',$2,$3,'local-bootstrap') RETURNING *",&[&id,&digest,&data])?;
        record(
            &mut tx,
            None,
            "credential.bootstrap",
            None,
            None,
            "created",
            Some(&id),
        )?;
        let out = public(&row);
        tx.commit()?;
        Ok(out)
    }
    pub(crate) fn auth_issue(
        &mut self,
        request: String,
        spec: Spec,
        digest: String,
    ) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        check(&mut tx)?;
        admin_lock(&mut tx)?;
        let p = guard(&mut tx, self.auth.as_ref(), Action::Admin, None, None)?
            .ok_or(Error::Unauthorized)?;
        let data = serde_json::to_value(&spec)?;
        if let Some(row) = tx.query_opt(
            "SELECT * FROM api_credentials WHERE request_id=$1",
            &[&request],
        )? {
            ensure!(
                row.get::<_, String>("token_hash") == digest
                    && row.get::<_, Value>("spec") == data
                    && row.get::<_, String>("created_by") == p.principal,
                Error::Conflict
            );
            let out = public(&row);
            tx.commit()?;
            return Ok(out);
        }
        ensure!(
            tx.query_one("SELECT count(*) FROM api_credentials", &[])?
                .get::<_, i64>(0)
                < 1024,
            Error::Conflict
        );
        ensure!(
            !tx.query_one(
                "SELECT EXISTS(SELECT 1 FROM api_credentials WHERE token_hash=$1)",
                &[&digest]
            )?
            .get::<_, bool>(0),
            Error::Conflict
        );
        if let Scope::Sessions { ids } = &spec.scope {
            for sid in ids {
                ensure!(
                    tx.query_one("SELECT EXISTS(SELECT 1 FROM sessions WHERE id=$1)", &[sid])?
                        .get::<_, bool>(0),
                    Error::Invalid
                );
            }
        }
        for env in &spec.environments {
            ensure!(
                tx.query_one(
                    "SELECT EXISTS(SELECT 1 FROM environments WHERE id=$1)",
                    &[env]
                )?
                .get::<_, bool>(0),
                Error::Invalid
            );
        }
        if let Some(expiry) = spec.expires_at {
            let now: f64 = tx
                .query_one("SELECT extract(epoch FROM clock_timestamp())::float8", &[])?
                .get(0);
            ensure!(
                expiry as f64 > now && expiry as f64 <= now + 31_536_000.0,
                Error::Invalid
            );
        }
        let id = format!("key_{}", uuid::Uuid::new_v4().simple());
        let row=tx.query_one("INSERT INTO api_credentials(id,request_id,token_hash,spec,created_by) VALUES($1,$2,$3,$4,$5) RETURNING *",&[&id,&request,&digest,&data,&p.principal])?;
        record(
            &mut tx,
            Some(&p),
            "credential.issue",
            None,
            Some(&request),
            "created",
            Some(&id),
        )?;
        let out = public(&row);
        tx.commit()?;
        Ok(out)
    }
    pub(crate) fn auth_revoke(&mut self, id: &str) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        check(&mut tx)?;
        admin_lock(&mut tx)?;
        let p = guard(&mut tx, self.auth.as_ref(), Action::Admin, None, None)?
            .ok_or(Error::Unauthorized)?;
        let row = tx
            .query_opt(
                "SELECT * FROM api_credentials WHERE id=$1 FOR UPDATE",
                &[&id],
            )?
            .ok_or(Error::Invalid)?;
        if !row.get::<_, bool>("revoked") {
            if matches!(
                row.get::<_, Value>("spec")["role"].as_str(),
                Some("administrator" | "service_administrator")
            ) {
                ensure!(tx.query_one("SELECT count(*) FROM api_credentials WHERE id<>$1 AND NOT revoked AND spec->>'role' IN ('administrator','service_administrator') AND ((spec->>'expires_at') IS NULL OR (spec->>'expires_at')::bigint>extract(epoch FROM clock_timestamp()))",&[&id])?.get::<_,i64>(0)>0,Error::Conflict);
            }
            tx.execute(
                "UPDATE api_credentials SET revoked=TRUE WHERE id=$1",
                &[&id],
            )?;
            record(
                &mut tx,
                Some(&p),
                "credential.revoke",
                None,
                None,
                "revoked",
                Some(id),
            )?;
        }
        let row = tx.query_one("SELECT * FROM api_credentials WHERE id=$1", &[&id])?;
        let out = public(&row);
        tx.commit()?;
        Ok(out)
    }
    pub(crate) fn auth_list(&mut self, after: &str) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        guard(&mut tx, self.auth.as_ref(), Action::Admin, None, None)?
            .ok_or(Error::Unauthorized)?;
        let rows = tx.query(
            "SELECT * FROM api_credentials WHERE id>$1 ORDER BY id LIMIT 101",
            &[&after],
        )?;
        let more = rows.len() > 100;
        let keys: Vec<_> = rows.iter().take(100).map(public).collect();
        let next = if more {
            keys.last().and_then(|v| v["id"].as_str())
        } else {
            None
        };
        let out = json!({"credentials":keys,"next_after":next});
        tx.commit()?;
        Ok(out)
    }
    pub(crate) fn auth_audit(&mut self, after: i64) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        guard(&mut tx, self.auth.as_ref(), Action::Admin, None, None)?
            .ok_or(Error::Unauthorized)?;
        let rows=tx.query("SELECT id,credential_id,principal,action,session_id,request_id,outcome,target,created::text FROM api_security_audit WHERE id>$1 ORDER BY id LIMIT 100",&[&after])?;
        let events:Vec<_>=rows.iter().map(|r|json!({"id":r.get::<_,i64>(0),"credential_id":r.get::<_,Option<String>>(1),"principal":r.get::<_,Option<String>>(2),"action":r.get::<_,String>(3),"session_id":r.get::<_,Option<String>>(4),"request_id":r.get::<_,Option<String>>(5),"outcome":r.get::<_,String>(6),"target":r.get::<_,Option<String>>(7),"created":r.get::<_,String>(8)})).collect();
        let next = events
            .last()
            .map(|v| v["id"].clone())
            .unwrap_or(json!(after));
        let out = json!({"events":events,"next_after":next});
        tx.commit()?;
        Ok(out)
    }
}
impl PostgresStore {
    /// Best-effort identified denial log; no raw path, token or payload.
    pub fn auth_denied(&self, action: Action) -> Result<()> {
        self.call(move |db| db.auth_denied(action))
    }
    pub fn authorized(&self, token: &str) -> Result<Self> {
        let mut store = self.clone();
        store.auth = Some(Context {
            digest: token_hash(token)?,
        });
        Ok(store)
    }
    pub fn authorize(
        &self,
        action: Action,
        sid: Option<&str>,
        environment: Option<&str>,
    ) -> Result<Identity> {
        let sid = sid.map(str::to_owned);
        let environment = environment.map(str::to_owned);
        self.call(move |db| db.auth_check(action, sid.as_deref(), environment.as_deref()))
    }
    /// Local operator bootstrap only. Existing/tombstoned credentials never reset.
    pub fn auth_bootstrap(&self, token: &str, principal: &str) -> Result<Value> {
        self.auth_bootstrap_expiring(token, principal, None)
    }
    pub fn auth_bootstrap_expiring(
        &self,
        token: &str,
        principal: &str,
        expires_at: Option<i64>,
    ) -> Result<Value> {
        id(principal, 64)?;
        let digest = token_hash(token)?;
        let principal = principal.to_owned();
        self.call(move |db| db.auth_bootstrap(digest, principal, expires_at))
    }
    pub fn auth_issue(&self, request: &str, spec: Spec, token: &str) -> Result<Value> {
        id(request, 100)?;
        ensure!(request != "bootstrap", Error::Invalid);
        spec.validate()?;
        let digest = token_hash(token)?;
        let request = request.to_owned();
        self.call(move |db| db.auth_issue(request, spec, digest))
    }
    /// Infrastructure-admin recovery only. Keeps bootstrap tombstone and all audit history.
    pub fn auth_recover(
        &self,
        request: &str,
        token: &str,
        principal: &str,
        expires_at: i64,
    ) -> Result<Value> {
        ensure!(self.auth.is_none(), Error::Forbidden);
        id(request, 80)?;
        id(principal, 64)?;
        let digest = token_hash(token)?;
        let request = format!("recovery:{request}");
        let spec = Spec {
            principal: principal.into(),
            role: Role::ServiceAdministrator,
            scope: Scope::All,
            environments: vec![],
            expires_at: Some(expires_at),
        };
        spec.validate()?;
        self.call(move|db|{let mut tx=db.client.transaction()?;check(&mut tx)?;admin_lock(&mut tx)?;
            ensure!(tx.query_one("SELECT EXISTS(SELECT 1 FROM api_credentials WHERE request_id='bootstrap')",&[])?.get::<_,bool>(0),Error::Unconfigured);
            if let Some(row)=tx.query_opt("SELECT * FROM api_credentials WHERE request_id=$1",&[&request])?{
                ensure!(row.get::<_,String>("token_hash")==digest&&row.get::<_,Value>("spec")["principal"]==spec.principal,Error::Conflict);
                return Ok(public(&row));
            }
            let now:f64=tx.query_one("SELECT extract(epoch FROM clock_timestamp())::float8",&[])?.get(0);
            ensure!(expires_at as f64>now&&expires_at as f64<=now+86400.0,Error::Invalid);
            let key=format!("key_{}",uuid::Uuid::new_v4().simple());
            let row=tx.query_one("INSERT INTO api_credentials(id,request_id,token_hash,spec,created_by) VALUES($1,$2,$3,$4,'infrastructure-recovery') RETURNING *",&[&key,&request,&digest,&json!(spec)])?;
            record(&mut tx,None,"credential.recovery",None,Some(&request),"created",Some(&key))?;
            let result=public(&row);tx.commit()?;Ok(result)})
    }
    pub fn auth_revoke(&self, key: &str) -> Result<Value> {
        id(key, 80)?;
        let key = key.to_owned();
        self.call(move |db| db.auth_revoke(&key))
    }
    pub fn auth_list(&self, after: &str) -> Result<Value> {
        if !after.is_empty() {
            id(after, 80)?;
        }
        let after = after.to_owned();
        self.call(move |db| db.auth_list(&after))
    }
    pub fn auth_audit(&self, after: i64) -> Result<Value> {
        ensure!(after >= 0, Error::Invalid);
        self.call(move |db| db.auth_audit(after))
    }
}
