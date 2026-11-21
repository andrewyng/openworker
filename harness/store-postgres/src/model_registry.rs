//! Cluster-owned model routes. Session admission pins a profile's choice inside the
//! same transaction as the session operation. Public reads never select the sealed key.
use crate::{
    auth::{self, Action},
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use openworker_runtime::{connections::Choice, model_api::Api};
use postgres::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Debug)]
pub enum RegistryError {
    NativeRouteRequired,
    ProfileUnavailable,
    ProviderDisabled,
    ImmutableProvider,
}
impl std::fmt::Display for RegistryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::NativeRouteRequired => "sandbox_loop_requires_an_openshell_native_provider",
            Self::ProfileUnavailable => "model_profile_unavailable",
            Self::ProviderDisabled => "model_provider_disabled",
            Self::ImmutableProvider => {
                "model_provider_already_registered_use_rotation_or_a_new_name"
            }
        })
    }
}
impl std::error::Error for RegistryError {}
#[derive(Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Provider {
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub allow_private_http: bool,
    pub name: String,
    pub base_url: String,
    pub api: Api,
    pub context_limit: usize,
}
impl Provider {
    pub fn endpoint(&self) -> openworker_runtime::connections::HostEndpoint {
        openworker_runtime::connections::HostEndpoint {
            context_limit: Some(self.context_limit),
            allow_private_http: self.allow_private_http,
            api: self.api,
            base_url: self.base_url.clone(),
            inference_options: Default::default(),
            integrations: Default::default(),
        }
    }
    pub fn installation_id(&self) -> Result<String> {
        Ok(openworker_runtime::bundle::digest(&serde_json::to_vec(
            self,
        )?))
    }
    pub fn selection(&self, choice: &Choice) -> Result<openworker_runtime::connections::Selection> {
        ensure!(choice.connection == self.name, SubmissionError::Conflict);
        Ok(openworker_runtime::connections::Selection {
            connection: self.name.clone(),
            model: choice.model.clone(),
            provider: self.api.kind(),
            notify_on_session_state: choice.notify_on_session_state.unwrap_or(false),
            identity: openworker_runtime::connections::host_identity(
                &self.name,
                self.api.kind(),
                &self.installation_id()?,
                Some(&self.endpoint()),
            )?,
        })
    }
    pub fn validate(&self) -> Result<()> {
        Choice::new(&self.name, "model").validate()?;
        if self.allow_private_http {
            openworker_runtime::model_api::private_base_url(&self.base_url)?;
        } else {
            openworker_runtime::model_api::base_url(&self.base_url)?;
        }
        ensure!(
            (1024..=2_000_000).contains(&self.context_limit),
            SubmissionError::InvalidRequest
        );
        Ok(())
    }
}
pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS model_providers(name TEXT PRIMARY KEY, spec JSONB NOT NULL, sealed BYTEA NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE);
    CREATE TABLE IF NOT EXISTS model_profiles(name TEXT PRIMARY KEY,choice JSONB NOT NULL,selection JSONB NOT NULL);
    ALTER TABLE service_operations ADD COLUMN IF NOT EXISTS pinned_model JSONB;")?;
    Ok(())
}
#[derive(Clone, Serialize, Deserialize)]
pub struct PinnedModel {
    pub choice: Choice,
    pub selection: openworker_runtime::connections::Selection,
}
pub(crate) fn resolve_profile(tx: &mut Transaction<'_>, name: &str) -> Result<PinnedModel> {
    let row = tx
        .query_opt(
            "SELECT choice,selection FROM model_profiles WHERE name=$1 FOR SHARE",
            &[&name],
        )?
        .ok_or(RegistryError::ProfileUnavailable)?;
    let choice: Choice = serde_json::from_value(row.get(0))?;
    if let Some(provider) = tx.query_opt(
        "SELECT enabled FROM model_providers WHERE name=$1 FOR SHARE",
        &[&choice.connection],
    )? {
        ensure!(provider.get::<_, bool>(0), RegistryError::ProviderDisabled);
    }
    Ok(PinnedModel {
        choice,
        selection: serde_json::from_value(row.get(1))?,
    })
}
impl PostgresStore {
    // Serialize replay checks with mutations across API replicas. A delayed retry of
    // an older key rotation or profile update must not undo a more recent change.
    pub(crate) fn admin_change<F>(
        &self,
        key: String,
        payload: Value,
        target: String,
        change: F,
    ) -> Result<Value>
    where
        F: FnOnce(&mut Transaction<'_>) -> Result<Value> + Send + 'static,
    {
        self.call(move |db| {
            let mut tx=db.client.transaction()?;
            let caller=auth::guard(&mut tx,db.auth.as_ref(),Action::Admin,None,None)?.ok_or(auth::Error::Unauthorized)?;
            tx.query_one("SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('app-registry-controls'))",&[])?;
            let input=json!([caller.principal,payload]);
            if let Some(row)=tx.query_opt("SELECT payload,result FROM service_management_receipts WHERE id=$1",&[&key])?{
                ensure!(row.get::<_,Value>(0)==input,SubmissionError::Conflict);
                return Ok(row.get(1));
            }
            let result=change(&mut tx)?;
            tx.execute("INSERT INTO service_management_receipts(id,payload,result) VALUES($1,$2,$3)",&[&key,&input,&result])?;
            auth::audit_target(&mut tx,Some(&caller),Action::Admin,&key,&target)?;
            tx.commit()?;
            Ok(result)
        })
    }
    pub fn provider_put(&self, p: Provider, sealed: Vec<u8>, key: &str) -> Result<Value> {
        p.validate()?;
        let stamp = sealed.get(..32).ok_or(SubmissionError::InvalidRequest)?;
        self.admin_change(key.into(), json!(["register",&p,stamp]),p.name.clone(),move |tx| {
            // The name fixes the endpoint, wire protocol and context ceiling for
            // existing explicit releases. Retarget a model profile to a new name.
            let spec=json!(p);
            if let Some(row)=tx.query_opt("SELECT spec,substring(sealed FROM 1 FOR 32),enabled FROM model_providers WHERE name=$1",&[&p.name])? {
                ensure!(row.get::<_,Value>(0)==spec && row.get::<_,Vec<u8>>(1)==sealed[..32] && row.get::<_,bool>(2),RegistryError::ImmutableProvider);
            } else {
                ensure!(tx.query_one("SELECT count(*) FROM model_providers",&[])?.get::<_,i64>(0)<16,SubmissionError::QueueFull);
                tx.execute("INSERT INTO model_providers(name,spec,sealed,enabled) VALUES($1,$2,$3,true)",&[&p.name,&spec,&sealed])?;
            }
            Ok(spec)
        })
    }
    pub fn provider_change(&self, name: &str, sealed: Option<Vec<u8>>, key: &str) -> Result<()> {
        let name = name.to_owned();
        let stamp = sealed
            .as_ref()
            .map(|v| v.get(..32).ok_or(SubmissionError::InvalidRequest))
            .transpose()?;
        self.admin_change(
            key.into(),
            json!(["provider-change", &name, stamp]),
            name.clone(),
            move |tx| {
                let changed = match sealed {
                    Some(cipher) => tx.execute(
                        "UPDATE model_providers SET sealed=$2 WHERE name=$1 AND enabled",
                        &[&name, &cipher],
                    )?,
                    None => tx.execute(
                        "UPDATE model_providers SET enabled=false WHERE name=$1",
                        &[&name],
                    )?,
                };
                ensure!(changed == 1, SubmissionError::NotFound);
                Ok(json!({}))
            },
        )?;
        Ok(())
    }
    pub fn model_profile_put(&self, name: &str, choice: Choice, key: &str) -> Result<Value> {
        self.set_profile_binding(name, choice, None, key)
    }

    pub fn set_profile_binding(
        &self,
        name: &str,
        choice: Choice,
        installed: Option<openworker_runtime::connections::Selection>,
        key: &str,
    ) -> Result<Value> {
        crate::service_catalog::name(name)?;
        choice.validate()?;
        let name = name.to_owned();
        self.admin_change(key.into(),json!(["profile",&name,&choice,&installed]),name.clone(),move|tx|{
            let selection=match installed {
                Some(s)=>{
                    ensure!(s.connection==choice.connection && s.model==choice.model && !s.identity.is_empty(),SubmissionError::InvalidRequest);
                    s
                },
                None=>{
                    let row=tx.query_opt("SELECT spec,enabled FROM model_providers WHERE name=$1 FOR SHARE",&[&choice.connection])?.ok_or(RegistryError::ProfileUnavailable)?;
                    ensure!(row.get::<_,bool>(1),RegistryError::ProviderDisabled);
                    let provider:Provider=serde_json::from_value(row.get(0))?;
                    provider.selection(&choice)?
                }
            };
            tx.execute("INSERT INTO model_profiles(name,choice,selection) VALUES($1,$2,$3) ON CONFLICT(name) DO UPDATE SET choice=excluded.choice,selection=excluded.selection",&[&name,&json!(choice),&json!(selection)])?;
            Ok(json!({"name":name,"model":choice}))
        })
    }
    pub fn model_catalog(&self) -> Result<Value> {
        self.call(|db| {
            let mut tx = db.client.transaction()?;
            auth::guard(&mut tx, db.auth.as_ref(), Action::Admin, None, None)?;
            let providers = tx
                .query(
                    "SELECT spec,enabled FROM model_providers ORDER BY name",
                    &[],
                )?
                .iter()
                .map(|r| {
                    let mut v: Value = r.get(0);
                    v["enabled"] = json!(r.get::<_, bool>(1));
                    v
                })
                .collect::<Vec<_>>();
            let profiles = tx
                .query("SELECT name,choice FROM model_profiles ORDER BY name", &[])?
                .iter()
                .map(|r| json!({"name":r.get::<_,String>(0),"model":r.get::<_,Value>(1)}))
                .collect::<Vec<_>>();
            tx.commit()?;
            Ok(json!({"providers":providers,"profiles":profiles}))
        })
    }
    pub fn model_routes(&self) -> Result<Vec<Provider>> {
        if self.auth.is_some() {
            self.authorize(Action::Read, None, None)?;
        }
        self.call(|db| {
            db.client
                .query("SELECT spec FROM model_providers ORDER BY name", &[])?
                .into_iter()
                .map(|r| Ok(serde_json::from_value(r.get(0))?))
                .collect()
        })
    }
    pub fn model_key_ciphertext(&self, name: &str) -> Result<Vec<u8>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let name = name.to_owned();
        self.call(move |db| {
            Ok(db
                .client
                .query_opt(
                    "SELECT sealed FROM model_providers WHERE name=$1 AND enabled",
                    &[&name],
                )?
                .ok_or(auth::Error::Forbidden)?
                .get(0))
        })
    }
    pub fn current_profile_model(&self, name: &str) -> Result<Choice> {
        let name = name.to_owned();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            if let Some(p) = auth::guard(&mut tx, db.auth.as_ref(), Action::Read, None, None)? {
                if p.role == auth::Role::App {
                    ensure!(
                        crate::app_agents::grants(&mut tx, &p.principal)?
                            .model_profiles
                            .contains(&name),
                        auth::Error::Forbidden
                    );
                }
            }
            let choice = resolve_profile(&mut tx, &name)?;
            tx.commit()?;
            Ok(choice.choice)
        })
    }
    pub fn operation_model(&self, id: &str) -> Result<Option<Choice>> {
        Ok(self.operation_model_pin(id)?.map(|pin| pin.choice))
    }
    pub fn operation_model_pin(&self, id: &str) -> Result<Option<PinnedModel>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let id = id.to_owned();
        self.call(move |db| {
            Ok(db
                .client
                .query_one(
                    "SELECT pinned_model FROM service_operations WHERE id=$1",
                    &[&id],
                )?
                .get::<_, Option<Value>>(0)
                .map(serde_json::from_value)
                .transpose()?)
        })
    }
}
