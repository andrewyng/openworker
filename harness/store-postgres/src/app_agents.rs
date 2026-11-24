//! Application identities own agent revisions independently of their current token.
use crate::{
    auth::{self, Action, Identity, Role},
    service_catalog::{Entry, Release},
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use openworker_runtime::connections::Choice;
use postgres::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Grants {
    #[serde(default)]
    pub tools: Vec<String>,
    #[serde(default)]
    pub models: Vec<Choice>,
    #[serde(default)]
    pub model_profiles: Vec<String>,
}
impl Grants {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.tools.len() <= 128 && self.models.len() <= 64 && self.model_profiles.len() <= 64,
            auth::Error::Invalid
        );
        for name in self.tools.iter().chain(&self.model_profiles) {
            crate::service_catalog::name(name)?;
        }
        for model in &self.models {
            model.validate()?;
        }
        Ok(())
    }
    fn permits(&self, r: &Release, restricted: &[String]) -> bool {
        restricted.iter().all(|t| self.tools.contains(t))
            && r.model_profile
                .as_ref()
                .is_none_or(|p| self.model_profiles.contains(p))
            && r.default_model
                .iter()
                .chain(&r.reviewer_model)
                .chain(&r.model_overrides)
                .all(|m| {
                    self.models
                        .iter()
                        .any(|g| g.connection == m.connection && g.model == m.model)
                })
            && (r.model_profile.is_some() || r.default_model.is_some())
    }
}
pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_app_grants(principal TEXT PRIMARY KEY,grants JSONB NOT NULL);
    CREATE TABLE IF NOT EXISTS service_app_tool_defaults(installation TEXT PRIMARY KEY,tools TEXT[] NOT NULL);
    CREATE TABLE IF NOT EXISTS service_app_agents(owner TEXT NOT NULL,name TEXT NOT NULL,revision INTEGER NOT NULL,release TEXT UNIQUE NOT NULL,shared_with TEXT[] NOT NULL DEFAULT '{}',PRIMARY KEY(owner,name,revision));")?;
    Ok(())
}
pub(crate) fn grants(tx: &mut Transaction<'_>, p: &str) -> Result<Grants> {
    Ok(serde_json::from_value(
        tx.query_opt(
            "SELECT grants FROM service_app_grants WHERE principal=$1 FOR SHARE",
            &[&p],
        )?
        .ok_or(auth::Error::Forbidden)?
        .get(0),
    )?)
}
fn restricted_tools(
    tx: &mut Transaction<'_>,
    installation: &str,
    tools: &[String],
) -> Result<Vec<String>> {
    let defaults = tx
        .query_opt(
            "SELECT tools FROM service_app_tool_defaults WHERE installation=$1",
            &[&installation],
        )?
        .map(|r| r.get::<_, Vec<String>>(0))
        .unwrap_or_default();
    Ok(tools
        .iter()
        .filter(|t| !defaults.contains(t))
        .cloned()
        .collect())
}
pub(crate) fn allow_release(tx: &mut Transaction<'_>, p: &Identity, r: &Release) -> Result<()> {
    let row = tx.query_opt(
        "SELECT owner,shared_with FROM service_app_agents WHERE release=$1 FOR SHARE",
        &[&r.id],
    )?;
    let shared = row
        .as_ref()
        .is_some_and(|v| v.get::<_, Vec<String>>(1).contains(&p.principal));
    if !shared {
        crate::service_catalog::allowed(p, &r.allowed_principals)?;
    }
    if p.role == Role::App {
        let installation: String = tx
            .query_one(
                "SELECT data->>'installation' FROM service_catalog WHERE kind='release' AND id=$1",
                &[&r.id],
            )?
            .get(0);
        let restricted = restricted_tools(tx, &installation, &r.tools)?;
        ensure!(
            grants(tx, &p.principal)?.permits(r, &restricted),
            auth::Error::Forbidden
        );
    }
    Ok(())
}
impl PostgresStore {
    /// Called only by a trusted service process after resolving the installed executable.
    pub fn install_app_tool_defaults(
        &self,
        installation: &str,
        mut tools: Vec<String>,
    ) -> Result<()> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        tools.sort();
        tools.dedup();
        let installation = installation.to_owned();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            tx.execute(
                "INSERT INTO service_app_tool_defaults VALUES($1,$2) ON CONFLICT DO NOTHING",
                &[&installation, &tools],
            )?;
            ensure!(
                tx.query_one(
                    "SELECT tools FROM service_app_tool_defaults WHERE installation=$1",
                    &[&installation]
                )?
                .get::<_, Vec<String>>(0)
                    == tools,
                SubmissionError::Conflict
            );
            tx.commit()?;
            Ok(())
        })
    }
    pub fn app_publish(
        &self,
        key: &str,
        name: &str,
        mut r: Release,
        installation: &str,
    ) -> Result<Entry> {
        crate::service_catalog::name(name)?;
        ensure!(!name.contains('@'), SubmissionError::InvalidRequest);
        let (name, key, installation) = (name.to_owned(), key.to_owned(), installation.to_owned());
        self.call(move|db|{
            let mut tx = db.client.transaction()?;
            let who = auth::guard(&mut tx, db.auth.as_ref(), Action::Submit, None, None)?
                .ok_or(auth::Error::Unauthorized)?;
            ensure!(who.role == Role::App, auth::Error::Forbidden);
            r.allowed_principals = vec![who.principal.clone()];
            let restricted = restricted_tools(&mut tx, &installation, &r.tools)?;
            ensure!(
                grants(&mut tx, &who.principal)?.permits(&r, &restricted),
                auth::Error::Forbidden
            );
            tx.query_one(
                "SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('service-catalog'))",
                &[],
            )?;
            for id in &r.runtime_profiles {
                let e = crate::service_catalog::entry(&mut tx, "profile", id)?;
                ensure!(e.installation == installation, SubmissionError::Conflict);
                let p: crate::service_catalog::Profile = serde_json::from_value(e.definition)?;
                ensure!(e.activated, auth::Error::Forbidden);
                crate::service_catalog::allowed(&who, &p.allowed_principals)?;
            }
            let payload = json!([&who.principal, &name, &r, &installation]);
            if let Some(old) = tx.query_opt(
                "SELECT payload,result FROM service_management_receipts WHERE id=$1",
                &[&key],
            )? {
                ensure!(old.get::<_, Value>(0) == payload, SubmissionError::Conflict);
                return Ok(serde_json::from_value(old.get(1))?);
            }
            let rev: i32 = tx
                .query_one(
                    "SELECT coalesce(max(revision),0)+1 FROM service_app_agents WHERE owner=$1 AND name=$2",
                    &[&who.principal, &name],
                )?
                .get(0);
            let identity =
                openworker_runtime::bundle::digest(&serde_json::to_vec(&(&who.principal, &name))?);
            r.id = format!("app-{}@{rev}", &identity[..48]);
            r.validate()?;
            ensure!(
                tx.query_one(
                    "SELECT count(*) FROM service_catalog WHERE kind='release'",
                    &[]
                )?
                .get::<_, i64>(0)
                    < 64,
                SubmissionError::QueueFull
            );
            let definition = json!(r);
            let e = Entry {
                id: r.id.clone(),
                kind: "release".into(),
                digest: openworker_runtime::bundle::digest(&serde_json::to_vec(&json!([
                    &definition,
                    &installation
                ]))?),
                definition,
                installation,
                activated: true,
            };
            tx.execute(
                "INSERT INTO service_catalog(kind,id,data,activated) VALUES('release',$1,$2,true)",
                &[&e.id, &json!(e)],
            )?;
            tx.execute(
                "INSERT INTO service_app_agents(owner,name,revision,release) VALUES($1,$2,$3,$4)",
                &[&who.principal, &name, &rev, &e.id],
            )?;
            tx.execute(
                "INSERT INTO service_management_receipts(id,payload,result) VALUES($1,$2,$3)",
                &[&key, &payload, &json!(e)],
            )?;
            auth::audit_target(&mut tx, Some(&who), Action::Publish, &key, &e.id)?;
            tx.commit()?;
            Ok(e)
        })
    }
    pub fn app_agents(&self) -> Result<Value> {
        self.call(|db|{
            let mut tx = db.client.transaction()?;
            let p = auth::guard(&mut tx, db.auth.as_ref(), Action::Read, None, None)?
                .ok_or(auth::Error::Unauthorized)?;
            let rows=tx.query("SELECT DISTINCT ON (owner,name) owner,name,revision,release,shared_with FROM service_app_agents WHERE owner=$1 OR $1=ANY(shared_with) OR $2 ORDER BY owner,name,revision DESC",&[&p.principal,&p.role.permits(Action::Admin)])?;
            let mut agents = vec![];
            for row in rows {
                let id: String = row.get(3);
                let e = crate::service_catalog::entry(&mut tx, "release", &id)?;
                let def = serde_json::from_value(e.definition)?;
                match allow_release(&mut tx, &p, &def) {
                    Ok(()) => {}
                    Err(err) if err.downcast_ref::<auth::Error>() == Some(&auth::Error::Forbidden) => {
                        continue
                    }
                    Err(err) => return Err(err),
                }
                let owner: String = row.get(0);
                let mut item = json!({"owner":owner,"name":row.get::<_,String>(1),"revision":row.get::<_,i32>(2),"releaseId":id});
                if p.role.permits(Action::Admin) || owner == p.principal {
                    item["sharedWith"] = json!(row.get::<_, Vec<String>>(4));
                }
                agents.push(item);
            }
            tx.commit()?;
            Ok(json!({"agents":agents}))
        })
    }
    pub fn share_app_agent(&self, id: &str, apps: Vec<String>, key: &str) -> Result<Value> {
        let id = id.to_owned();
        ensure!(apps.len() <= 100, auth::Error::Invalid);
        self.admin_change(
            key.into(),
            json!(["app.share", &id, &apps]),
            id.clone(),
            move |tx| {
                for app in &apps {
                    grants(tx, app)?;
                }
                ensure!(
                    tx.execute(
                        "UPDATE service_app_agents SET shared_with=$2 WHERE release=$1",
                        &[&id, &apps]
                    )? == 1,
                    SubmissionError::NotFound
                );
                Ok(json!({"releaseId":id,"sharedWith":apps}))
            },
        )
    }
}
