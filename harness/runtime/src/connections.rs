//! Trusted model connections. Secrets never enter the session/event journal.
use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{collections::BTreeMap, path::Path, sync::Mutex};
use zeroize::Zeroizing;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Choice {
    pub connection: String,
    pub model: String,
}
impl Choice {
    pub fn new(provider: impl Into<String>, name: impl Into<String>) -> Self {
        Self {
            connection: provider.into(),
            model: name.into(),
        }
    }
    pub fn validate(&self) -> Result<()> {
        if !crate::bundle::safe_name(&self.connection)
            || self.connection.len() > 64
            || self.model.is_empty()
            || self.model.len() > 256
            || !self
                .model
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"-._:/".contains(&c))
        {
            bail!("Invalid model connection or model ID");
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Selection {
    #[serde(default)]
    pub provider: crate::provider::Kind,
    pub connection: String,
    pub model: String,
    pub identity: String,
}
#[derive(Default, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Defaults {
    pub agent: Option<Choice>,
    pub reviewer: Option<Choice>,
}
struct State {
    db: Connection,
    memory: BTreeMap<String, Zeroizing<String>>,
    host_references: std::collections::BTreeSet<String>,
    tokens: BTreeMap<String, std::sync::Arc<crate::provider_auth::Tokens>>,
}
pub struct Manager {
    scope: String,
    state: Mutex<State>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Set {
    id: String,
    label: String,
    provider: crate::provider::Kind,
    api_key: String,
    persistence: String,
    #[serde(default)]
    api: Option<crate::model_api::Api>,
    #[serde(default)]
    base_url: Option<String>,
}
impl Manager {
    /// Trusted server bootstrap only; not exposed by the model or stdio protocol.
    /// A deployment-owned identity lets separate nodes resolve the same session.
    /// Only the key is rotated; changing identity makes existing sessions refuse.
    pub fn register_host(
        &self,
        id: &str,
        provider: crate::provider::Kind,
        identity: &str,
        key: Zeroizing<String>,
    ) -> Result<()> {
        self.host(id, provider, identity, Some(key))
    }
    /// API-only identity reference. Does not read or hold a provider credential.
    pub fn register_host_reference(
        &self,
        id: &str,
        provider: crate::provider::Kind,
        identity: &str,
    ) -> Result<()> {
        self.host(id, provider, identity, None)
    }
    fn host(
        &self,
        id: &str,
        provider: crate::provider::Kind,
        identity: &str,
        key: Option<Zeroizing<String>>,
    ) -> Result<()> {
        Choice {
            connection: id.into(),
            model: "validation".into(),
        }
        .validate()?;
        if identity.is_empty()
            || identity.len() > 128
            || !identity
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_.:".contains(&b))
            || key.as_ref().is_some_and(|key| {
                key.len() < 8 || key.len() > 4096 || !key.bytes().all(|b| (33..=126).contains(&b))
            })
        {
            bail!("Invalid host model connection");
        }
        let identity = crate::bundle::digest(
            format!("host-model-v1:{identity}:{}:{id}", provider.name()).as_bytes(),
        );
        let mut state = self.state.lock().unwrap();
        use rusqlite::OptionalExtension;
        let custom_endpoint: bool = state.db.query_row(
            "SELECT EXISTS(SELECT 1 FROM endpoints WHERE id=?1)",
            [id],
            |r| r.get(0),
        )?;
        if custom_endpoint {
            bail!("Host bootstrap cannot replace an SDK provider; use a separate connection ID");
        }
        let previous: Option<String> = state
            .db
            .query_row("SELECT provider FROM connections WHERE id=?1", [id], |r| {
                r.get(0)
            })
            .optional()?;
        if previous.as_deref().is_some_and(|p| p != provider.name()) {
            bail!("Host connection provider changed; use a new connection ID");
        }
        state.db.execute("INSERT INTO connections(id,label,identity,persistence,secret,provider) VALUES(?1,?1,?2,'memory',NULL,?3) ON CONFLICT(id) DO UPDATE SET identity=excluded.identity,persistence='memory',secret=NULL",params![id,identity,provider.name()])?;
        state.host_references.insert(id.into());
        if let Some(key) = key {
            state.memory.insert(id.into(), key);
        } else {
            state.memory.remove(id);
        }
        Ok(())
    }
    pub fn open(root: &Path) -> Result<Self> {
        crate::platform::private_directory(root)?;
        let path = root.join("model-connections.sqlite");
        if path.exists() && crate::platform::linked(&path.symlink_metadata()?) {
            bail!("Credential database cannot be a link");
        }
        let db = Connection::open(path)?;
        db.execute_batch("PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY,label TEXT NOT NULL,identity TEXT NOT NULL,persistence TEXT NOT NULL,secret BLOB);
            CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);")?;
        let has_provider = db
            .prepare("PRAGMA table_info(connections)")?
            .query_map([], |r| r.get::<_, String>(1))?
            .collect::<std::result::Result<Vec<_>, _>>()?
            .iter()
            .any(|name| name == "provider");
        if !has_provider {
            db.execute_batch(
                "ALTER TABLE connections ADD COLUMN provider TEXT NOT NULL DEFAULT 'openai'",
            )?;
        }
        db.execute_batch("CREATE TABLE IF NOT EXISTS endpoints(id TEXT PRIMARY KEY, api TEXT NOT NULL, base_url TEXT NOT NULL);")?;
        Ok(Self {
            scope: root.canonicalize()?.to_string_lossy().into(),
            state: Mutex::new(State {
                db,
                memory: BTreeMap::new(),
                host_references: Default::default(),
                tokens: Default::default(),
            }),
        })
    }
    pub fn list(&self) -> Result<Value> {
        let state = self.state.lock().unwrap();
        let mut statement = state.db.prepare(
            "SELECT id,label,persistence,secret IS NOT NULL,provider FROM connections ORDER BY id",
        )?;
        let rows = statement.query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, bool>(3)?,
                r.get::<_, String>(4)?,
            ))
        })?;
        let mut connections = vec![];
        for row in rows {
            let (id, label, persistence, stored, provider) = row?;
            use rusqlite::OptionalExtension;
            let endpoint: Option<(String, String)> = state
                .db
                .query_row(
                    "SELECT api,base_url FROM endpoints WHERE id=?1",
                    [&id],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?;
            let (api, base_url) = endpoint
                .map(|(a, b)| (serde_json::from_str::<Value>(&a).unwrap_or(Value::Null), b))
                .unwrap_or_else(|| {
                    let api = if provider == "anthropic" {
                        crate::model_api::Api::Anthropic
                    } else {
                        crate::model_api::Api::Responses
                    };
                    (json!(api), api.default_url().into())
                });
            connections.push(json!({"id":id,"name":id,"label":label,"provider":provider,"api":api,"base_url":base_url,"persistence":persistence,"configured":persistence=="none"||stored||state.memory.contains_key(&id)||state.tokens.get(&id).is_some_and(|t|t.ready())}));
        }
        Ok(
            json!({"connections":connections,"secure_storage_available":cfg!(any(windows,target_os="macos"))}),
        )
    }
    pub fn set(&self, value: Value) -> Result<Value> {
        // Never return serde's raw invalid-value diagnostics for a secret-bearing request.
        let mut input: Set = serde_json::from_value(value)
            .map_err(|_| anyhow::anyhow!("Invalid connection settings"))?;
        let key = Zeroizing::new(std::mem::take(&mut input.api_key));
        let api = input
            .api
            .unwrap_or(if input.provider == crate::provider::Kind::Anthropic {
                crate::model_api::Api::Anthropic
            } else {
                crate::model_api::Api::Responses
            });
        if api.kind() != input.provider {
            bail!("API and provider transport disagree");
        }
        let base_url =
            crate::model_api::base_url(input.base_url.as_deref().unwrap_or(api.default_url()))?;
        let api_json = serde_json::to_string(&api)?;
        Choice {
            connection: input.id.clone(),
            model: "validation".into(),
        }
        .validate()?;
        if input.label.trim().is_empty()
            || input.label.len() > 100
            || input.label.chars().any(char::is_control)
            || (!["none", "access_token"].contains(&input.persistence.as_str()) && key.len() < 8)
            || (["none", "access_token"].contains(&input.persistence.as_str()) && !key.is_empty())
            || key.len() > 4096
            || !key.bytes().all(|c| (33..=126).contains(&c))
            || !["secure", "memory", "none", "access_token"].contains(&input.persistence.as_str())
        {
            bail!("Invalid connection settings");
        }
        let mut state = self.state.lock().unwrap();
        use rusqlite::OptionalExtension;
        let existing: Option<String> = state
            .db
            .query_row(
                "SELECT provider FROM connections WHERE id=?1",
                [&input.id],
                |r| r.get(0),
            )
            .optional()?;
        if state
            .db
            .query_row(
                "SELECT persistence FROM connections WHERE id=?1",
                [&input.id],
                |r| r.get::<_, String>(0),
            )
            .optional()?
            .as_deref()
            == Some("access_token")
        {
            bail!("Use provider/auth/update to rotate access tokens");
        }
        #[cfg(target_os = "macos")]
        let was_secure = state
            .db
            .query_row(
                "SELECT persistence='secure' FROM connections WHERE id=?1",
                [&input.id],
                |r| r.get::<_, bool>(0),
            )
            .optional()?
            .unwrap_or(false);
        if existing
            .as_deref()
            .is_some_and(|p| p != input.provider.name())
        {
            bail!("Connection provider is immutable; create a different connection");
        }
        let endpoint: Option<(String, String)> = state
            .db
            .query_row(
                "SELECT api,base_url FROM endpoints WHERE id=?1",
                [&input.id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        if let Some((old_api, old_url)) = endpoint {
            if old_api != api_json || old_url != base_url {
                bail!("Provider API and endpoint are immutable; use a new provider name");
            }
        } else if existing.is_some()
            && (api
                != if input.provider == crate::provider::Kind::Anthropic {
                    crate::model_api::Api::Anthropic
                } else {
                    crate::model_api::Api::Responses
                }
                || base_url != api.default_url())
        {
            bail!("Existing connection endpoint is immutable; use a new provider name");
        }
        let encrypted = if input.persistence == "secure" {
            Some(protect(key.as_bytes(), &self.entropy(&input.id), false)?)
        } else {
            None
        };
        let transaction = state.db.transaction()?;
        transaction.execute("INSERT INTO connections(id,label,identity,persistence,secret,provider) VALUES(?1,?2,?3,?4,?5,?6)
            ON CONFLICT(id) DO UPDATE SET label=excluded.label,persistence=excluded.persistence,secret=excluded.secret",
            params![input.id,input.label,crate::id("connection"),input.persistence,encrypted,input.provider.name()])?;
        transaction.execute(
            "INSERT OR IGNORE INTO endpoints(id,api,base_url) VALUES(?1,?2,?3)",
            params![input.id, api_json, base_url],
        )?;
        transaction.commit()?;
        #[cfg(target_os = "macos")]
        if was_secure && input.persistence != "secure" {
            forget_keychain(&self.entropy(&input.id))?;
        }
        if input.persistence == "memory" {
            state.memory.insert(input.id, key);
        } else {
            state.memory.remove(&input.id);
        }
        Ok(json!({"saved":true}))
    }
    pub fn remove(&self, id: &str) -> Result<Value> {
        let mut state = self.state.lock().unwrap();
        #[cfg(target_os = "macos")]
        if state
            .db
            .query_row(
                "SELECT persistence='secure' FROM connections WHERE id=?1",
                [id],
                |r| r.get::<_, bool>(0),
            )
            .unwrap_or(false)
        {
            forget_keychain(&self.entropy(id))?;
        }
        state
            .db
            .execute("DELETE FROM connections WHERE id=?1", [id])?;
        state.memory.remove(id);
        state.tokens.remove(id);
        state.host_references.remove(id);
        state
            .db
            .execute("DELETE FROM endpoints WHERE id=?1", [id])?;
        Ok(json!({"removed":true}))
    }
    pub fn defaults(&self) -> Result<Defaults> {
        use rusqlite::OptionalExtension;
        let raw: Option<String> = self
            .state
            .lock()
            .unwrap()
            .db
            .query_row("SELECT value FROM settings WHERE id=1", [], |r| r.get(0))
            .optional()?;
        raw.map(|s| serde_json::from_str(&s).map_err(Into::into))
            .unwrap_or_else(|| Ok(Defaults::default()))
    }
    pub fn set_defaults(&self, v: Value) -> Result<Value> {
        let defaults: Defaults =
            serde_json::from_value(v).map_err(|_| anyhow::anyhow!("Invalid model defaults"))?;
        for choice in [&defaults.agent, &defaults.reviewer].into_iter().flatten() {
            self.resolve(choice)?;
        }
        self.state.lock().unwrap().db.execute("INSERT INTO settings(id,value) VALUES(1,?1) ON CONFLICT(id) DO UPDATE SET value=excluded.value",[serde_json::to_string(&defaults)?])?;
        Ok(json!({"defaults":defaults}))
    }
    pub fn resolve(&self, choice: &Choice) -> Result<Selection> {
        choice.validate()?;
        let (identity, provider): (String, String) = self
            .state
            .lock()
            .unwrap()
            .db
            .query_row(
                "SELECT identity,provider FROM connections WHERE id=?1",
                [&choice.connection],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .map_err(|_| {
                anyhow::anyhow!("Credentials required: configure the selected connection")
            })?;
        let selected = Selection {
            provider: serde_json::from_value(json!(provider))
                .context("Unsupported stored provider")?,
            connection: choice.connection.clone(),
            model: choice.model.clone(),
            identity,
        };
        let host_reference = self
            .state
            .lock()
            .unwrap()
            .host_references
            .contains(&choice.connection);
        if !host_reference && !self.credentials_ready(&selected) {
            bail!("Provider authentication required");
        }
        Ok(selected)
    }
    pub fn key(&self, selected: &Selection) -> Result<Zeroizing<String>> {
        let state = self.state.lock().unwrap();
        let (identity, kind, secret, provider): (String, String, Option<Vec<u8>>, String) = state
            .db
            .query_row(
                "SELECT identity,persistence,secret,provider FROM connections WHERE id=?1",
                [&selected.connection],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .map_err(|_| anyhow::anyhow!("Credentials required: selected connection is missing"))?;
        if identity != selected.identity || provider != selected.provider.name() {
            bail!("Connection identity changed; create a new session");
        }
        if kind == "none" {
            return Ok(Zeroizing::new(String::new()));
        }
        if kind == "memory" {
            return state
                .memory
                .get(&selected.connection)
                .map(|s| Zeroizing::new(s.to_string()))
                .context("Credentials required: resupply this memory-only key");
        }
        let encrypted = secret.context("Credentials required")?;
        let bytes = Zeroizing::new(protect(
            &encrypted,
            &self.entropy(&selected.connection),
            true,
        )?);
        Ok(Zeroizing::new(
            std::str::from_utf8(&bytes)
                .context("Invalid protected credential")?
                .into(),
        ))
    }
    pub fn credentials_ready(&self, selected: &Selection) -> bool {
        match self.access_tokens(selected) {
            Ok(Some(tokens)) => tokens.ready(),
            Ok(None) => self.key(selected).is_ok(),
            Err(_) => false,
        }
    }
    pub fn access_tokens(
        &self,
        selected: &Selection,
    ) -> Result<Option<std::sync::Arc<crate::provider_auth::Tokens>>> {
        self.endpoint(selected)?; // Includes the pinned connection identity check.
        let mut state = self.state.lock().unwrap();
        let kind: String = state.db.query_row(
            "SELECT persistence FROM connections WHERE id=?1",
            [&selected.connection],
            |r| r.get(0),
        )?;
        Ok(if kind == "access_token" {
            Some(
                state
                    .tokens
                    .entry(selected.connection.clone())
                    .or_default()
                    .clone(),
            )
        } else {
            None
        })
    }
    pub fn register_access_auth(
        &self,
        name: &str,
        refresh: Option<crate::provider_auth::Refresh>,
    ) -> Result<()> {
        Choice::new(name, "validation").validate()?;
        let mut state = self.state.lock().unwrap();
        let (kind, provider): (String, String) = state.db.query_row(
            "SELECT persistence,provider FROM connections WHERE id=?1",
            [name],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        anyhow::ensure!(
            provider == "openai",
            "Access-token authentication requires a Bearer API"
        );
        anyhow::ensure!(
            kind == "none" || kind == "access_token",
            "Use a new provider name for access-token authentication"
        );
        state.db.execute(
            "UPDATE connections SET persistence='access_token',secret=NULL WHERE id=?1",
            [name],
        )?;
        state.tokens.entry(name.into()).or_default().bind(refresh);
        Ok(())
    }
    pub fn update_access_token(
        &self,
        name: &str,
        token: crate::provider_auth::AccessToken,
    ) -> Result<()> {
        let mut state = self.state.lock().unwrap();
        let kind: String = state.db.query_row(
            "SELECT persistence FROM connections WHERE id=?1",
            [name],
            |r| r.get(0),
        )?;
        anyhow::ensure!(
            kind == "access_token",
            "Register access-token authentication first"
        );
        state.tokens.entry(name.into()).or_default().update(token);
        Ok(())
    }
    fn entropy(&self, id: &str) -> Vec<u8> {
        format!("openworker-model-credentials-v1:{}:{id}", self.scope).into_bytes()
    }
    /// Endpoint identity is immutable for the life of a named connection. Keys can rotate.
    pub fn endpoint(&self, selected: &Selection) -> Result<(crate::model_api::Api, String)> {
        use rusqlite::OptionalExtension;
        let state = self.state.lock().unwrap();
        let identity: String = state
            .db
            .query_row(
                "SELECT identity FROM connections WHERE id=?1",
                [&selected.connection],
                |r| r.get(0),
            )
            .context("Selected model provider is missing")?;
        if identity != selected.identity {
            bail!("Connection identity changed; create a new session");
        }
        let endpoint: Option<(String, String)> = state
            .db
            .query_row(
                "SELECT api,base_url FROM endpoints WHERE id=?1",
                [&selected.connection],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        let (api, base) = match endpoint {
            Some((api, url)) => (
                serde_json::from_str::<crate::model_api::Api>(&api)?,
                crate::model_api::base_url(&url)?,
            ),
            None => {
                let api = if selected.provider == crate::provider::Kind::Anthropic {
                    crate::model_api::Api::Anthropic
                } else {
                    crate::model_api::Api::Responses
                };
                (api, api.default_url().into())
            }
        };
        Ok((api, format!("{base}/{}", api.path())))
    }
    /// Harness-owned provider registration; never journal this secret-bearing request.
    pub fn set_provider(&self, value: Value) -> Result<Value> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Input {
            name: String,
            api: crate::model_api::Api,
            base_url: Option<String>,
            credentials: Option<Credentials>,
            #[serde(default)]
            access_token_auth: bool,
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Credentials {
            api_key: String,
            storage: Option<String>,
        }
        let input: Input = serde_json::from_value(value)
            .map_err(|_| anyhow::anyhow!("Invalid model provider configuration"))?;
        Choice::new(&input.name, "validation").validate()?;
        anyhow::ensure!(
            !input.access_token_auth
                || (input.credentials.is_none()
                    && input.api.kind() == crate::provider::Kind::Openai),
            "Access tokens require a Bearer API without static credentials"
        );
        use rusqlite::OptionalExtension;
        let existing = {
            let state = self.state.lock().unwrap();
            state.db.query_row("SELECT c.provider,e.api,e.base_url FROM connections c LEFT JOIN endpoints e ON c.id=e.id WHERE c.id=?1",[&input.name],|r|Ok((r.get::<_,String>(0)?,r.get::<_,Option<String>>(1)?,r.get::<_,Option<String>>(2)?))).optional()?
        };
        if input.api == crate::model_api::Api::ChatCompletions
            && input.base_url.is_none()
            && existing
                .as_ref()
                .and_then(|(_, _, url)| url.as_ref())
                .is_none()
        {
            bail!("Chat Completions requires an explicit base_url; provider names do not select endpoints");
        }
        let base_url = crate::model_api::base_url(
            input
                .base_url
                .as_deref()
                .or_else(|| existing.as_ref().and_then(|(_, _, url)| url.as_deref()))
                .unwrap_or(input.api.default_url()),
        )?;
        if let Some((provider, api, url)) = &existing {
            let old_api = api
                .as_deref()
                .map(serde_json::from_str::<crate::model_api::Api>)
                .transpose()?
                .unwrap_or(if provider == "anthropic" {
                    crate::model_api::Api::Anthropic
                } else {
                    crate::model_api::Api::Responses
                });
            if old_api != input.api || url.as_deref().unwrap_or(old_api.default_url()) != base_url {
                bail!("Provider API and endpoint are immutable; use a new provider name");
            }
            // An empty Settings key field must never erase an existing credential.
            if input.credentials.is_none() {
                if input.access_token_auth {
                    let kind: String = self.state.lock().unwrap().db.query_row(
                        "SELECT persistence FROM connections WHERE id=?1",
                        [&input.name],
                        |r| r.get(0),
                    )?;
                    if kind != "access_token" {
                        self.register_access_auth(&input.name, None)?;
                    }
                }
                return Ok(json!({"saved":true,"credentials_unchanged":true}));
            }
        }
        let (key, storage) = match input.credentials {
            Some(c) => {
                let storage = c.storage.unwrap_or_else(|| "secure".into());
                if !["secure", "memory"].contains(&storage.as_str()) {
                    bail!("Credentials require secure or memory storage");
                }
                (c.api_key, storage)
            }
            None => (
                String::new(),
                if input.access_token_auth {
                    "access_token"
                } else {
                    "none"
                }
                .into(),
            ),
        };
        self.set(json!({"id":input.name,"label":input.name,"provider":input.api.kind(),"api":input.api,"base_url":base_url,"api_key":key,"persistence":storage}))
    }
}
#[cfg(not(any(windows, target_os = "macos")))]
fn protect(_: &[u8], _: &[u8], _: bool) -> Result<Vec<u8>> {
    bail!("Secure credential persistence requires Windows DPAPI or macOS Keychain; choose memory explicitly. No plaintext fallback.")
}
#[cfg(target_os = "macos")]
fn protect(data: &[u8], entropy: &[u8], decrypt: bool) -> Result<Vec<u8>> {
    use security_framework::passwords::{get_generic_password, set_generic_password};
    let account = crate::bundle::digest(entropy);
    if decrypt {
        anyhow::ensure!(
            data == b"macos-keychain-v1",
            "Invalid Keychain credential reference"
        );
        get_generic_password("OpenWorker Model Providers", &account).map_err(|_| {
            anyhow::anyhow!("macOS Keychain credential unavailable; no plaintext fallback")
        })
    } else {
        set_generic_password("OpenWorker Model Providers", &account, data)
            .map_err(|_| anyhow::anyhow!("macOS Keychain storage failed; no plaintext fallback"))?;
        Ok(b"macos-keychain-v1".to_vec())
    }
}
#[cfg(target_os = "macos")]
fn forget_keychain(entropy: &[u8]) -> Result<()> {
    match security_framework::passwords::delete_generic_password(
        "OpenWorker Model Providers",
        &crate::bundle::digest(entropy),
    ) {
        Ok(()) => Ok(()),
        Err(e) if e.code() == -25300 => Ok(()), // errSecItemNotFound
        Err(_) => bail!("macOS Keychain could not remove the provider credential"),
    }
}
#[cfg(windows)]
fn protect(data: &[u8], entropy: &[u8], decrypt: bool) -> Result<Vec<u8>> {
    use windows_sys::Win32::{Foundation::LocalFree, Security::Cryptography::*};
    let input = CRYPT_INTEGER_BLOB {
        cbData: data.len() as u32,
        pbData: data.as_ptr().cast_mut(),
    };
    let extra = CRYPT_INTEGER_BLOB {
        cbData: entropy.len() as u32,
        pbData: entropy.as_ptr().cast_mut(),
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: std::ptr::null_mut(),
    };
    let ok = unsafe {
        if decrypt {
            CryptUnprotectData(
                &input,
                std::ptr::null_mut(),
                &extra,
                std::ptr::null(),
                std::ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        } else {
            CryptProtectData(
                &input,
                std::ptr::null(),
                &extra,
                std::ptr::null(),
                std::ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        }
    };
    if ok == 0 {
        bail!("Windows secure credential operation failed; no plaintext fallback");
    }
    let result =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec() };
    unsafe {
        std::ptr::write_bytes(output.pbData, 0, output.cbData as usize);
        LocalFree(output.pbData.cast());
    }
    Ok(result)
}
