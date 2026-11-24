//! Trusted host registration, configuration and per-session activation.
use crate::{connections::Selection, provider_adapters::Transport, safety};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::BTreeMap, sync::Arc};

pub(crate) fn lifecycle_kind(kind: &str) -> bool {
    matches!(
        kind,
        "session_created"
            | "turn_started"
            | "tool_queued"
            | "tool_started"
            | "tool_finished"
            | "model_attempt_started"
            | "model_attempt_finished"
            | "turn_completed"
            | "session_closed"
            | "interaction_required"
            | "interaction_resolved"
    )
}
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Preference {
    #[serde(default = "enabled_by_default")]
    pub enabled: bool,
    #[serde(flatten)]
    pub options: serde_json::Map<String, Value>,
}
impl<'de> Deserialize<'de> for Preference {
    fn deserialize<D: serde::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(untagged)]
        enum Saved {
            Object {
                #[serde(default = "enabled_by_default")]
                enabled: bool,
                #[serde(flatten)]
                options: serde_json::Map<String, Value>,
            },
            Legacy(bool),
        }
        Ok(match Saved::deserialize(deserializer)? {
            Saved::Object { enabled, options } => Self { enabled, options },
            Saved::Legacy(enabled) => enabled.into(),
        })
    }
}
/// Validate new requests strictly while allowing the decoder to recover older saved definitions.
pub fn validate_entries(value: &Value) -> Result<()> {
    let entries = value
        .as_object()
        .context("Integrations must be an object")?;
    ensure!(
        entries.len() <= 64 && serde_json::to_vec(value)?.len() <= 16384,
        "Too many integration options"
    );
    for (id, options) in entries {
        validate_id(id)?;
        let options = options
            .as_object()
            .context("Integration entries must be objects; use enabled: false to disable")?;
        if let Some(enabled) = options.get("enabled") {
            ensure!(
                enabled.is_boolean(),
                "Integration enabled must be a boolean"
            );
        }
    }
    Ok(())
}
fn enabled_by_default() -> bool {
    true
}
impl From<bool> for Preference {
    fn from(enabled: bool) -> Self {
        Self {
            enabled,
            options: Default::default(),
        }
    }
}
pub type Preferences = BTreeMap<String, Preference>;
pub type Settings = BTreeMap<String, Value>;
pub type Policies = BTreeMap<String, Mode>;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Mode {
    OnByDefault,
    OffByDefault,
    Required,
    Forbidden,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CheckMode {
    Shadow,
    Enforce,
}

pub type AppCheckModes = BTreeMap<String, BTreeMap<String, CheckMode>>;

#[derive(Default)]
pub struct PolicyRevision {
    pub revision: i64,
    pub policies: Policies,
    pub app_check_modes: AppCheckModes,
}
pub type ReadPolicy = Arc<dyn Fn() -> Result<PolicyRevision> + Send + Sync>;
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    Default,
    Harness,
    Provider,
    Agent,
    Session,
    Required,
    Forbidden,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Decision {
    pub enabled: bool,
    pub source: Source,
    pub revision: i64,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Admission {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_connection: Option<String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub overrides: Settings,
    #[serde(default)]
    pub cluster_policy: bool,
    #[serde(default)]
    pub may_check_results: bool,
    #[serde(default)]
    pub observers: std::collections::BTreeSet<String>,
    #[serde(default)]
    pub choices: BTreeMap<String, Decision>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub applied: BTreeMap<String, Decision>,
    #[serde(default)]
    pub bindings: BTreeMap<String, String>,
}
impl Admission {
    pub fn is_empty(&self) -> bool {
        self.choices.is_empty() && self.bindings.is_empty()
    }
}

/// The journal sequence is stable across retries. Observers receive lifecycle metadata only.
#[derive(Clone, Debug)]
pub struct Event<'a> {
    pub state: &'a str,
    pub session_id: &'a str,
    pub sequence: i64,
    pub turn_id: Option<&'a str>,
    pub interaction_id: Option<&'a str>,
    pub call_id: Option<&'a str>,
    pub decision: Option<&'a str>,
    pub agent_context: Option<&'a crate::provider_adapters::AgentContext>,
}
pub type Delivery<'a> = futures_util::future::BoxFuture<'a, Result<()>>;
pub trait Observer: Send + Sync {
    fn notify<'a>(&'a self, event: Event<'a>) -> Delivery<'a>;
}
pub enum Instance {
    Observer(Arc<dyn Observer>),
    /// A provider's inference protocol is configured for this named connection.
    /// Activation independently controls lifecycle notifications.
    Provider {
        connection: String,
        transport: Arc<dyn Transport>,
    },
    /// Blocking hooks through the existing safety engine and its handler registry.
    Checks(safety::Config),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    Lifecycle,
    ProviderLifecycle,
    BlockingChecks,
}
pub type Factory = fn(&Value, &safety::Registry) -> Result<Instance>;
#[derive(Clone, Copy, Serialize)]
pub struct Registration {
    pub version: &'static str,
    pub capability: Capability,
    #[serde(skip)]
    pub configure: Factory,
}
#[derive(Clone, Default)]
pub struct Registry(BTreeMap<String, Registration>);
pub fn validate_id(id: &str) -> Result<()> {
    ensure!(
        !id.is_empty()
            && id.len() <= 80
            && id
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'),
        "Invalid integration ID"
    );
    Ok(())
}
impl Registry {
    pub fn register(&mut self, id: &str, registration: Registration) -> Result<()> {
        validate_id(id)?;
        ensure!(
            !registration.version.is_empty(),
            "Missing integration version for {id}"
        );
        ensure!(!self.0.contains_key(id), "Duplicate integration {id}");
        self.0.insert(id.into(), registration);
        Ok(())
    }
    pub fn catalog(&self) -> &BTreeMap<String, Registration> {
        &self.0
    }
    pub fn configure(&self, settings: &Settings, policy: ReadPolicy) -> Result<Configured> {
        self.configure_scoped(settings, policy, None)
    }
    fn configure_for_provider(
        &self,
        settings: &Settings,
        policy: ReadPolicy,
        connection: &str,
    ) -> Result<Configured> {
        self.configure_scoped(settings, policy, Some(connection))
    }
    fn configure_scoped(
        &self,
        settings: &Settings,
        policy: ReadPolicy,
        connection: Option<&str>,
    ) -> Result<Configured> {
        validate_entries(&serde_json::to_value(settings)?)?;
        let handlers = Arc::new(safety::Registry::default());
        let mut providers = BTreeMap::new();
        let mut instances = BTreeMap::new();
        let mut bindings = BTreeMap::new();
        let mut defaults = Preferences::new();
        for (id, raw) in settings {
            let mut config = raw
                .as_object()
                .context("Integration options must be an object")?
                .clone();
            let enabled = config
                .remove("enabled")
                .map(|v| v.as_bool().context("Integration enabled must be a boolean"))
                .transpose()?
                .unwrap_or(true);
            defaults.insert(id.clone(), enabled.into());
            let config = Value::Object(config);
            let registration = self
                .0
                .get(id)
                .with_context(|| format!("Integration {id} is not registered in this runtime"))?;
            if registration.capability == Capability::ProviderLifecycle && connection.is_none() {
                continue;
            }
            let mut instance = (registration.configure)(&config, &handlers)
                .with_context(|| format!("Invalid configuration for integration {id}"))?;
            if let Instance::Provider {
                connection: bound, ..
            } = &mut instance
            {
                *bound = connection
                    .context("Provider integration requires a model connection")?
                    .to_owned();
            }
            match &instance {
                Instance::Observer(_) => ensure!(
                    registration.capability == Capability::Lifecycle,
                    "Lifecycle observer unavailable"
                ),
                Instance::Provider { connection, .. } => {
                    ensure!(
                        registration.capability == Capability::ProviderLifecycle,
                        "Unexpected provider capability"
                    );
                    ensure!(
                        providers.insert(connection.clone(), id.clone()).is_none(),
                        "Multiple integrations for provider {connection}"
                    );
                }
                Instance::Checks(checks) => {
                    ensure!(
                        registration.capability == Capability::BlockingChecks
                            && cfg!(feature = "guardrails"),
                        "Unexpected check capability"
                    );
                    checks.validate()?;
                    ensure!(
                        !checks.hooks.is_empty()
                            && checks
                                .hooks
                                .iter()
                                .all(|h| h.when != safety::HookPhase::OnEvent),
                        "Blocking integration needs decision hooks"
                    );
                    for hook in &checks.hooks {
                        handlers.handler(&hook.handler)?;
                    }
                }
            }
            bindings.insert(
                id.clone(),
                crate::bundle::digest(&serde_json::to_vec(&(id, registration.version, config))?),
            );
            instances.insert(id.clone(), instance);
        }
        Ok(Configured {
            defaults,
            provider_defaults: Default::default(),
            settings: settings.clone(),
            provider_configs: Default::default(),
            scopes: Default::default(),
            providers,
            cluster_policy: false,
            handlers,
            instances,
            bindings,
            policy,
            catalog: self.clone(),
        })
    }
}
pub struct Configured {
    defaults: Preferences,
    provider_defaults: std::collections::BTreeSet<String>,
    settings: Settings,
    provider_configs: std::sync::RwLock<BTreeMap<String, Arc<Configured>>>,
    scopes: std::sync::Mutex<BTreeMap<String, Arc<Configured>>>,
    providers: BTreeMap<String, String>,
    #[cfg_attr(not(feature = "guardrails"), allow(dead_code))]
    pub(crate) handlers: Arc<safety::Registry>,
    instances: BTreeMap<String, Instance>,
    bindings: BTreeMap<String, String>,
    policy: ReadPolicy,
    pub catalog: Registry,
    pub cluster_policy: bool,
}
impl Default for Configured {
    fn default() -> Self {
        Self {
            cluster_policy: false,
            providers: Default::default(),
            handlers: Default::default(),
            defaults: Default::default(),
            provider_defaults: Default::default(),
            settings: Default::default(),
            provider_configs: Default::default(),
            scopes: Default::default(),
            instances: Default::default(),
            bindings: Default::default(),
            policy: Arc::new(|| Ok(Default::default())),
            catalog: Default::default(),
        }
    }
}
impl Configured {
    fn scope(&self, overrides: &Settings, connection: Option<&str>) -> Result<Arc<Configured>> {
        let mut merged = self.settings.clone();
        for (id, value) in overrides {
            ensure!(self.catalog.0.contains_key(id), "Unknown integration {id}");
            let mut options = merged
                .get(id)
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            options.extend(
                value
                    .as_object()
                    .context("Integration options must be an object")?
                    .clone(),
            );
            merged.insert(id.clone(), Value::Object(options));
        }
        let key = crate::bundle::digest(&serde_json::to_vec(&(connection, &merged))?);
        let mut scopes = self.scopes.lock().unwrap();
        if let Some(scope) = scopes.get(&key) {
            return Ok(scope.clone());
        }
        ensure!(
            scopes.len() < 256,
            "Integration configuration capacity reached"
        );
        let mut scope = self
            .catalog
            .configure_scoped(&merged, self.policy.clone(), connection)?;
        scope.cluster_policy = self.cluster_policy;
        scope.provider_defaults = self.provider_defaults.clone();
        for instance in scope.instances.values() {
            if let Instance::Checks(checks) = instance {
                for hook in &checks.hooks {
                    if self.handlers.handler(&hook.handler).is_err() {
                        self.handlers.register(
                            hook.handler.clone(),
                            scope.handlers.handler(&hook.handler)?,
                        )?;
                    }
                }
            }
        }
        scope.handlers = self.handlers.clone();
        let scope = Arc::new(scope);
        scopes.insert(key, scope.clone());
        Ok(scope)
    }
    pub fn checks_for(&self, admission: &Admission) -> Result<safety::Config> {
        self.checks_for_app(admission, || Ok(None))
    }
    pub fn checks_for_app(
        &self,
        admission: &Admission,
        app: impl FnOnce() -> Result<Option<String>>,
    ) -> Result<safety::Config> {
        let policy = (self.policy)()?;
        let modes = if policy.app_check_modes.is_empty() {
            None
        } else {
            app()?.and_then(|id| policy.app_check_modes.get(&id))
        };
        self.checks_for_revision(admission, &policy, modes)
    }
    fn checks_for_revision(
        &self,
        admission: &Admission,
        policy: &PolicyRevision,
        modes: Option<&BTreeMap<String, CheckMode>>,
    ) -> Result<safety::Config> {
        if let Some(scoped) = admission
            .model_connection
            .as_ref()
            .and_then(|c| self.provider_configs.read().unwrap().get(c).cloned())
        {
            return scoped.checks_for_revision(admission, policy, modes);
        }
        if !admission.overrides.is_empty() {
            let scoped = self.scope(&admission.overrides, admission.model_connection.as_deref())?;
            let mut inner = admission.clone();
            inner.overrides.clear();
            return scoped.checks_for_revision(&inner, policy, modes);
        }
        let mut decisions = self.resolve_with(admission, policy)?;
        let mut shadow = std::collections::BTreeSet::new();
        for (id, mode) in modes.into_iter().flatten() {
            if policy.policies.get(id) == Some(&Mode::Forbidden) {
                continue;
            }
            ensure!(
                matches!(self.instances.get(id), Some(Instance::Checks(_)))
                    && self
                        .bindings
                        .get(id)
                        .is_some_and(|pin| admission.bindings.get(id) == Some(pin)),
                "App check {id} has no matching installed configuration"
            );
            decisions.insert(
                id.clone(),
                Decision {
                    enabled: true,
                    source: Source::Required,
                    revision: policy.revision,
                },
            );
            if *mode == CheckMode::Shadow {
                shadow.insert(id.as_str());
            }
        }
        self.checks_with_shadow(&decisions, &shadow)
    }
    pub fn configured_ids(&self) -> impl Iterator<Item = String> {
        let mut ids: std::collections::BTreeSet<_> = self.instances.keys().cloned().collect();
        for provider in self.provider_configs.read().unwrap().values() {
            ids.extend(provider.instances.keys().cloned());
        }
        ids.into_iter()
    }
    pub(crate) fn may_inspect_output(&self) -> bool {
        self.instances.values().any(|i|matches!(i,Instance::Checks(checks) if checks.hooks.iter().any(|h|matches!(h.when,safety::HookPhase::BeforeOutput|safety::HookPhase::BeforeTool))))
            || self.provider_configs.read().unwrap().values().any(|p| p.may_inspect_output())
    }
    pub fn provider(&self, connection: &str) -> Option<(String, Arc<dyn Transport>)> {
        if let Some(scoped) = self.provider_configs.read().unwrap().get(connection) {
            return scoped.provider(connection);
        }
        let id = self.providers.get(connection)?;
        match self.instances.get(id)? {
            Instance::Provider { transport, .. } => Some((id.clone(), transport.clone())),
            _ => None,
        }
    }
    pub fn remove_provider(&self, connection: &str) {
        self.provider_configs.write().unwrap().remove(connection);
    }
    fn merged_provider_settings(&self, settings: &Settings) -> Result<Settings> {
        validate_entries(&serde_json::to_value(settings)?)?;
        let mut merged = self.settings.clone();
        merged.retain(|id, _| {
            self.catalog
                .0
                .get(id)
                .is_none_or(|r| r.capability != Capability::ProviderLifecycle)
                || settings.contains_key(id)
        });
        for (id, value) in settings {
            let options = value
                .as_object()
                .context("Integration options must be an object")?;
            let mut inherited = merged
                .get(id)
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            // Presence without enabled explicitly selects on at this scope.
            inherited.insert("enabled".into(), Value::Bool(true));
            inherited.extend(options.clone());
            merged.insert(id.clone(), Value::Object(inherited));
        }
        Ok(merged)
    }
    pub fn validate_provider(&self, connection: &str, settings: &Settings) -> Result<()> {
        self.catalog.configure_for_provider(
            &self.merged_provider_settings(settings)?,
            self.policy.clone(),
            connection,
        )?;
        Ok(())
    }
    pub fn configure_provider(&self, connection: &str, settings: &Settings) -> Result<()> {
        let merged = self.merged_provider_settings(settings)?;
        let mut cache = self.provider_configs.write().unwrap();
        if let Some(existing) = cache.get(connection) {
            ensure!(
                existing.settings == merged,
                "Provider integration configuration changed"
            );
            return Ok(());
        }
        let mut scoped =
            self.catalog
                .configure_for_provider(&merged, self.policy.clone(), connection)?;
        scoped.cluster_policy = self.cluster_policy;
        // Reuse handler instances in the host registry for ingress and tool checks.
        for instance in scoped.instances.values() {
            if let Instance::Checks(checks) = instance {
                for hook in &checks.hooks {
                    if self.handlers.handler(&hook.handler).is_err() {
                        self.handlers.register(
                            hook.handler.clone(),
                            scoped.handlers.handler(&hook.handler)?,
                        )?;
                    }
                }
            }
        }
        scoped.handlers = self.handlers.clone();
        scoped.provider_defaults = settings.keys().cloned().collect();
        cache.insert(connection.into(), Arc::new(scoped));
        Ok(())
    }
    pub fn model_preference(
        &self,
        preferences: &mut Preferences,
        choice: Option<&crate::connections::Choice>,
    ) -> Result<()> {
        if let Some((choice, enabled)) =
            choice.and_then(|c| c.notify_on_session_state.map(|enabled| (c, enabled)))
        {
            if let Some((id, _)) = self.provider(&choice.connection) {
                if let Some(previous) = preferences.get(&id) {
                    ensure!(
                        previous.enabled == enabled,
                        "Conflicting activation preferences for {id}"
                    );
                } else {
                    preferences.insert(id, enabled.into());
                }
            }
        }
        Ok(())
    }
    pub fn admit(&self, agent: &Preferences, session: &Preferences) -> Result<Admission> {
        self.admit_for_model(agent, session, None)
    }
    pub fn admit_for_model(
        &self,
        agent: &Preferences,
        session: &Preferences,
        selection: Option<&Selection>,
    ) -> Result<Admission> {
        if let Some(scoped) = selection.and_then(|s| {
            self.provider_configs
                .read()
                .unwrap()
                .get(&s.connection)
                .cloned()
        }) {
            return scoped.admit_for_model(agent, session, selection);
        }
        let mut overrides = Settings::new();
        for preferences in [agent, session] {
            for (id, pref) in preferences {
                if pref.options.is_empty() {
                    continue;
                }
                ensure!(
                    !self.cluster_policy,
                    "Cluster integration options are operator-owned"
                );
                let value = overrides
                    .entry(id.clone())
                    .or_insert_with(|| serde_json::json!({}));
                value.as_object_mut().unwrap().extend(pref.options.clone());
            }
        }
        ensure!(
            serde_json::to_vec(&overrides)?.len() <= 16384,
            "Integration options exceed 16 KiB"
        );
        if !overrides.is_empty() {
            let scoped = self.scope(&overrides, selection.map(|s| s.connection.as_str()))?;
            let clear = |prefs: &Preferences| {
                prefs
                    .iter()
                    .map(|(k, v)| (k.clone(), v.enabled.into()))
                    .collect()
            };
            let mut admission =
                scoped.admit_for_model(&clear(agent), &clear(session), selection)?;
            admission.overrides = overrides;
            return Ok(admission);
        }
        let policy = (self.policy)()?;
        let mut admitted = Admission {
            model_connection: selection.map(|s| s.connection.clone()),
            cluster_policy: self.cluster_policy,
            bindings: self.bindings.clone(),
            ..Default::default()
        };
        for (id, instance) in &self.instances {
            match instance {
                Instance::Provider { connection, .. } => {
                    if selection.is_some_and(|s| &s.connection == connection) {
                        admitted.observers.insert(id.clone());
                    }
                }
                Instance::Observer(_) => {
                    admitted.observers.insert(id.clone());
                }
                Instance::Checks(checks) => {
                    admitted.may_check_results |= checks
                        .hooks
                        .iter()
                        .any(|h| h.when == safety::HookPhase::AfterTool);
                }
            }
        }
        for id in self
            .instances
            .keys()
            .chain(policy.policies.keys())
            .chain(agent.keys())
            .chain(session.keys())
        {
            validate_id(id)?;
            let (enabled, source) = if let Some(on) = session.get(id) {
                (on.enabled, Source::Session)
            } else if let Some(on) = agent.get(id) {
                (on.enabled, Source::Agent)
            } else if let Some(on) = self.defaults.get(id) {
                (
                    on.enabled,
                    if self.provider_defaults.contains(id) {
                        Source::Provider
                    } else {
                        Source::Harness
                    },
                )
            } else {
                (
                    policy.policies.get(id) == Some(&Mode::OnByDefault),
                    Source::Default,
                )
            };
            admitted.choices.insert(
                id.clone(),
                Decision {
                    enabled,
                    source,
                    revision: policy.revision,
                },
            );
        }
        admitted.applied = self.resolve_with(&admitted, &policy)?;
        Ok(admitted)
    }
    pub fn resolve(&self, admission: &Admission) -> Result<BTreeMap<String, Decision>> {
        self.resolve_with(admission, &(self.policy)()?)
    }
    fn resolve_with(
        &self,
        admission: &Admission,
        current: &PolicyRevision,
    ) -> Result<BTreeMap<String, Decision>> {
        if let Some(scoped) = admission
            .model_connection
            .as_ref()
            .and_then(|c| self.provider_configs.read().unwrap().get(c).cloned())
        {
            return scoped.resolve_with(admission, current);
        }
        if !admission.overrides.is_empty() {
            let scoped = self.scope(&admission.overrides, admission.model_connection.as_deref())?;
            let mut inner = admission.clone();
            inner.overrides.clear();
            return scoped.resolve_with(&inner, current);
        }
        let mut decisions = admission.choices.clone();
        for (id, mode) in &current.policies {
            let (enabled, source) = match mode {
                Mode::Required => (true, Source::Required),
                Mode::Forbidden => (false, Source::Forbidden),
                _ => continue,
            };
            decisions.insert(
                id.clone(),
                Decision {
                    enabled,
                    source,
                    revision: current.revision,
                },
            );
        }
        for (id, decision) in &mut decisions {
            if !decision.enabled {
                continue;
            }
            if self
                .catalog
                .0
                .get(id)
                .is_some_and(|r| r.capability == Capability::ProviderLifecycle)
                && !self.instances.contains_key(id)
            {
                ensure!(
                    !matches!(decision.source, Source::Session | Source::Agent),
                    "Integration {id} is unavailable for the selected provider"
                );
                decision.enabled = false;
                continue;
            }
            ensure!(
                self.instances.contains_key(id),
                "Enabled integration {id} is unavailable"
            );
            if let Some(Instance::Provider { connection, .. }) = self.instances.get(id) {
                if admission.model_connection.as_ref() != Some(connection) {
                    ensure!(
                        !matches!(decision.source, Source::Session | Source::Agent),
                        "Integration {id} belongs to a different model provider"
                    );
                    decision.enabled = false;
                    continue;
                }
            }
            let pin=admission.bindings.get(id).with_context(||format!("Session has no trusted configuration for integration {id}; reopen it with a configured host"))?;
            ensure!(
                self.bindings.get(id) == Some(pin),
                "Configuration changed for integration {id}"
            );
        }
        Ok(decisions)
    }
    pub async fn deliver(&self, admission: &Admission, sid: &str, receipt: &Value) -> Result<()> {
        self.deliver_with_model(admission, sid, receipt, None, None)
            .await
    }
    pub(crate) async fn deliver_with_model(
        &self,
        admission: &Admission,
        sid: &str,
        receipt: &Value,
        binding: Option<(&crate::connections::Manager, &Selection)>,
        agent_context: Option<&crate::provider_adapters::AgentContext>,
    ) -> Result<()> {
        let scoped = admission
            .model_connection
            .as_ref()
            .and_then(|c| self.provider_configs.read().unwrap().get(c).cloned());
        let scope = scoped.as_deref().unwrap_or(self);
        let options_scope = if admission.overrides.is_empty() {
            None
        } else {
            Some(scope.scope(&admission.overrides, admission.model_connection.as_deref())?)
        };
        let effective = options_scope.as_deref().unwrap_or(scope);
        let mut inner = admission.clone();
        inner.overrides.clear();
        effective
            .deliver_scoped(&inner, sid, receipt, binding, agent_context)
            .await
    }

    async fn deliver_scoped(
        &self,
        admission: &Admission,
        sid: &str,
        receipt: &Value,
        binding: Option<(&crate::connections::Manager, &Selection)>,
        agent_context: Option<&crate::provider_adapters::AgentContext>,
    ) -> Result<()> {
        let id = receipt["hook"]["integration"]
            .as_str()
            .context("Missing integration ID")?;
        let decisions = self.resolve(admission)?;
        if !decisions.get(id).is_some_and(|d| d.enabled) {
            return Ok(());
        }
        let sequence = receipt["cursor"]
            .as_i64()
            .context("Missing event sequence")?;
        let record = &receipt["record"];
        let facts = &record["facts"];
        let state = match record["kind"].as_str() {
            Some("tool_started") => "waiting_tool_completion",
            Some("interaction_required" | "interaction_resolved") => {
                facts["state_change"].as_str().unwrap_or("")
            }
            Some(other) => other,
            None => return Ok(()),
        };
        if !matches!(
            state,
            "session_created"
                | "turn_started"
                | "tool_queued"
                | "tool_finished"
                | "model_attempt_started"
                | "model_attempt_finished"
                | "turn_completed"
                | "session_closed"
                | "waiting_tool_completion"
                | "waiting_user_response"
                | "user_response_done"
                | "waiting_user_approval"
                | "user_approval_done"
        ) {
            return Ok(());
        }
        let event = Event {
            state,
            session_id: sid,
            sequence,
            turn_id: receipt["turnId"].as_str(),
            interaction_id: facts["interaction_id"].as_str(),
            call_id: facts["call_id"].as_str(),
            decision: facts["decision"].as_str(),
            agent_context,
        };
        match self.instances.get(id) {
            Some(Instance::Observer(observer)) => observer.notify(event).await,
            Some(Instance::Provider {
                connection,
                transport,
            }) => {
                let (manager, selected) = binding.context("Missing model connection")?;
                ensure!(
                    &selected.connection == connection,
                    "Provider binding changed"
                );
                transport.notify(manager, selected, event).await
            }
            _ => anyhow::bail!("Missing configured lifecycle integration {id}"),
        }
    }
    pub fn checks(&self, decisions: &BTreeMap<String, Decision>) -> Result<safety::Config> {
        self.checks_with_shadow(decisions, &Default::default())
    }

    fn checks_with_shadow(
        &self,
        decisions: &BTreeMap<String, Decision>,
        shadow: &std::collections::BTreeSet<&str>,
    ) -> Result<safety::Config> {
        let mut checks = safety::Config::default();
        for (id, d) in decisions {
            if d.enabled {
                if let Some(Instance::Checks(config)) = self.instances.get(id) {
                    let mut config = config.clone();
                    config
                        .native_hook_names
                        .extend(config.hooks.iter().map(|h| h.name.clone()));
                    if shadow.contains(id.as_str()) {
                        config
                            .shadow_hook_names
                            .extend(config.native_hook_names.iter().cloned());
                    }
                    checks = checks.merge(&config)?;
                }
            }
        }
        Ok(checks)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::RwLock;
    struct Sink;
    impl Observer for Sink {
        fn notify<'a>(&'a self, _: Event<'a>) -> Delivery<'a> {
            Box::pin(async { Ok(()) })
        }
    }
    fn registration() -> Registration {
        Registration {
            version: "1",
            capability: Capability::Lifecycle,
            configure: |_, _| Ok(Instance::Observer(Arc::new(Sink))),
        }
    }
    fn fixture() -> (Configured, Arc<RwLock<PolicyRevision>>) {
        let mut registry = Registry::default();
        registry.register("first", registration()).unwrap();
        registry.register("second", registration()).unwrap();
        assert!(registry.register("first", registration()).is_err());
        assert_eq!(registry.catalog().len(), 2);
        let state = Arc::new(RwLock::new(PolicyRevision::default()));
        let read = state.clone();
        let settings = BTreeMap::from([("first".into(), serde_json::json!({}))]);
        let mut configured = registry
            .configure(
                &settings,
                Arc::new(move || {
                    let value = read.read().unwrap();
                    Ok(PolicyRevision {
                        revision: value.revision,
                        policies: value.policies.clone(),
                        app_check_modes: value.app_check_modes.clone(),
                    })
                }),
            )
            .unwrap();
        configured.defaults.clear();
        (configured, state)
    }
    #[test]
    fn all_twelve_choices_and_agent_inheritance() {
        let (host, state) = fixture();
        use Mode::*;
        for (mode, expected) in [
            (OnByDefault, [true, true, false]),
            (OffByDefault, [false, true, false]),
            (Required, [true, true, true]),
            (Forbidden, [false, false, false]),
        ] {
            state.write().unwrap().policies.insert("first".into(), mode);
            for (pref, expected) in [None, Some(true), Some(false)].into_iter().zip(expected) {
                let request = pref
                    .map(|b| BTreeMap::from([("first".into(), b.into())]))
                    .unwrap_or_default();
                let s = host.admit(&Preferences::new(), &request).unwrap();
                assert_eq!(
                    host.resolve(&s).unwrap()["first"].enabled,
                    expected,
                    "{mode:?} {pref:?}"
                );
            }
        }
        state.write().unwrap().policies.clear();
        let agent = BTreeMap::from([("first".into(), true.into())]);
        let session = BTreeMap::from([("first".into(), false.into())]);
        let inherited = host.admit(&agent, &Preferences::new()).unwrap();
        let explicit = host.admit(&agent, &session).unwrap();
        assert!(host.resolve(&inherited).unwrap()["first"].enabled);
        assert!(!host.resolve(&explicit).unwrap()["first"].enabled);
    }
    #[test]
    fn forced_changes_apply_after_resume_and_defaults_only_on_admission() {
        let (host, state) = fixture();
        let old = host
            .admit(&Preferences::new(), &Preferences::new())
            .unwrap();
        let checkpoint = serde_json::to_vec(&old).unwrap();
        for (i, (mode, expected)) in [
            (Mode::OnByDefault, false),
            (Mode::Required, true),
            (Mode::Forbidden, false),
            (Mode::OnByDefault, false),
        ]
        .into_iter()
        .enumerate()
        {
            *state.write().unwrap() = PolicyRevision {
                revision: i as i64 + 1,
                policies: BTreeMap::from([("first".into(), mode)]),
                ..Default::default()
            };
            let resumed = serde_json::from_slice(&checkpoint).unwrap();
            for admission in [&old, &resumed] {
                let resolved = host.resolve(admission).unwrap();
                assert_eq!(resolved["first"].enabled, expected);
                assert_eq!(
                    resolved["first"].revision,
                    if matches!(mode, Mode::Required | Mode::Forbidden) {
                        i as i64 + 1
                    } else {
                        0
                    }
                );
            }
            let fresh = host
                .admit(&Preferences::new(), &Preferences::new())
                .unwrap();
            assert_eq!(
                host.resolve(&fresh).unwrap()["first"].enabled,
                mode != Mode::Forbidden
            );
        }
        state
            .write()
            .unwrap()
            .policies
            .insert("second".into(), Mode::Required);
        assert!(host
            .resolve(&old)
            .unwrap_err()
            .to_string()
            .contains("second"));
    }
    #[test]
    fn object_contract_rejects_literals_but_recovers_saved_preferences() {
        for value in [
            serde_json::json!({"first": true}),
            serde_json::json!({"first": false}),
            serde_json::json!({"first": {"enabled":"yes"}}),
        ] {
            assert!(validate_entries(&value).is_err());
        }
        for value in [
            serde_json::json!({"first": {}}),
            serde_json::json!({"first": {"enabled":true}}),
            serde_json::json!({"first": {"enabled":false}}),
        ] {
            validate_entries(&value).unwrap();
        }
        let saved: Preferences =
            serde_json::from_value(serde_json::json!({"first": false})).unwrap();
        assert_eq!(
            serde_json::to_value(saved).unwrap(),
            serde_json::json!({"first":{"enabled":false}})
        );
    }

    #[test]
    fn provider_agent_and_session_override_harness_defaults_independently() {
        let (mut host, policy) = fixture();
        host.defaults.insert("first".into(), false.into());
        host.settings
            .insert("first".into(), serde_json::json!({"enabled":false}));
        let selected: Selection = serde_json::from_value(serde_json::json!({
            "provider":"openai", "connection":"local", "model":"demo", "identity":"fixture"
        }))
        .unwrap();
        host.configure_provider(
            "local",
            &BTreeMap::from([("first".into(), serde_json::json!({}))]),
        )
        .unwrap();
        let inherited = host
            .admit_for_model(&Preferences::new(), &Preferences::new(), Some(&selected))
            .unwrap();
        assert!(inherited.choices["first"].enabled);
        assert_eq!(inherited.choices["first"].source, Source::Provider);
        let off = BTreeMap::from([("first".into(), false.into())]);
        let on = BTreeMap::from([("first".into(), true.into())]);
        let agent = host
            .admit_for_model(&off, &Preferences::new(), Some(&selected))
            .unwrap();
        assert!(!agent.choices["first"].enabled);
        let session = host.admit_for_model(&off, &on, Some(&selected)).unwrap();
        assert!(session.choices["first"].enabled);
        assert_eq!(session.choices["first"].source, Source::Session);
        policy
            .write()
            .unwrap()
            .policies
            .insert("first".into(), Mode::Forbidden);
        assert!(!host.resolve(&session).unwrap()["first"].enabled);
        policy
            .write()
            .unwrap()
            .policies
            .insert("first".into(), Mode::Required);
        assert!(host.resolve(&agent).unwrap()["first"].enabled);
    }

    #[test]
    fn options_merge_and_cluster_clients_cannot_replace_trusted_configuration() {
        let (mut host, _) = fixture();
        let agent: Preferences =
            serde_json::from_value(serde_json::json!({"first":{"setting":"agent"}})).unwrap();
        let session: Preferences =
            serde_json::from_value(serde_json::json!({"first":{"setting":"session"}})).unwrap();
        let admission = host.admit(&agent, &session).unwrap();
        assert_eq!(admission.overrides["first"]["setting"], "session");
        let saved = serde_json::to_vec(&admission).unwrap();
        let recovered: Admission = serde_json::from_slice(&saved).unwrap();
        assert!(host.resolve(&recovered).unwrap()["first"].enabled);
        host.cluster_policy = true;
        assert!(host.admit(&agent, &session).is_err());
    }
}
