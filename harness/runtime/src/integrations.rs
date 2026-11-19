//! Trusted host registration, configuration and per-session activation.
use crate::safety;
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
pub type Preferences = BTreeMap<String, bool>;
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

#[derive(Default)]
pub struct PolicyRevision {
    pub revision: i64,
    pub policies: Policies,
}
pub type ReadPolicy = Arc<dyn Fn() -> Result<PolicyRevision> + Send + Sync>;
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    Default,
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
}
pub type Delivery<'a> = futures_util::future::BoxFuture<'a, Result<()>>;
pub trait Observer: Send + Sync {
    fn notify<'a>(&'a self, event: Event<'a>) -> Delivery<'a>;
}
pub enum Instance {
    Observer(Arc<dyn Observer>),
    /// Blocking hooks through the existing safety engine and its handler registry.
    Checks(safety::Config),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    Lifecycle,
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
        ensure!(settings.len() <= 64, "Too many integrations");
        let handlers = safety::Registry::default();
        let mut instances = BTreeMap::new();
        let mut bindings = BTreeMap::new();
        for (id, config) in settings {
            let registration = self
                .0
                .get(id)
                .with_context(|| format!("Integration {id} is not registered in this runtime"))?;
            let instance = (registration.configure)(config, &handlers)
                .with_context(|| format!("Invalid configuration for integration {id}"))?;
            match &instance {
                Instance::Observer(_) => ensure!(
                    registration.capability == Capability::Lifecycle
                        && cfg!(feature = "guardrails"),
                    "Lifecycle observer unavailable"
                ),
                Instance::Checks(checks) => {
                    ensure!(
                        registration.capability == Capability::BlockingChecks,
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
    #[cfg_attr(not(feature = "guardrails"), allow(dead_code))]
    pub(crate) handlers: safety::Registry,
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
            handlers: Default::default(),
            instances: Default::default(),
            bindings: Default::default(),
            policy: Arc::new(|| Ok(Default::default())),
            catalog: Default::default(),
        }
    }
}
impl Configured {
    pub fn configured_ids(&self) -> impl Iterator<Item = &str> {
        self.instances.keys().map(String::as_str)
    }
    pub(crate) fn may_inspect_output(&self) -> bool {
        self.instances.values().any(|i|matches!(i,Instance::Checks(checks) if checks.hooks.iter().any(|h|matches!(h.when,safety::HookPhase::BeforeOutput|safety::HookPhase::BeforeTool))))
    }
    pub fn admit(&self, agent: &Preferences, session: &Preferences) -> Result<Admission> {
        let policy = (self.policy)()?;
        let mut admitted = Admission {
            cluster_policy: self.cluster_policy,
            bindings: self.bindings.clone(),
            ..Default::default()
        };
        for (id, instance) in &self.instances {
            match instance {
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
                (*on, Source::Session)
            } else if let Some(on) = agent.get(id) {
                (*on, Source::Agent)
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
        for (id, decision) in &decisions {
            if !decision.enabled {
                continue;
            }
            ensure!(
                self.instances.contains_key(id),
                "Enabled integration {id} is unavailable"
            );
            let pin=admission.bindings.get(id).with_context(||format!("Session has no trusted configuration for integration {id}; reopen it with a configured host"))?;
            ensure!(
                self.bindings.get(id) == Some(pin),
                "Configuration changed for integration {id}"
            );
        }
        Ok(decisions)
    }
    pub async fn deliver(&self, admission: &Admission, sid: &str, receipt: &Value) -> Result<()> {
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
        };
        match self.instances.get(id) {
            Some(Instance::Observer(observer)) => observer.notify(event).await,
            _ => anyhow::bail!("Missing configured lifecycle integration {id}"),
        }
    }
    pub fn checks(&self, decisions: &BTreeMap<String, Decision>) -> Result<safety::Config> {
        let mut checks = safety::Config::default();
        for (id, d) in decisions {
            if d.enabled {
                if let Some(Instance::Checks(config)) = self.instances.get(id) {
                    checks = checks.merge(config)?;
                    checks
                        .native_hook_names
                        .extend(config.hooks.iter().map(|h| h.name.clone()));
                }
            }
        }
        Ok(checks)
    }
}

#[cfg(all(test, feature = "guardrails"))]
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
        let settings = BTreeMap::from([("first".into(), Value::Null)]);
        let configured = registry
            .configure(
                &settings,
                Arc::new(move || {
                    let value = read.read().unwrap();
                    Ok(PolicyRevision {
                        revision: value.revision,
                        policies: value.policies.clone(),
                    })
                }),
            )
            .unwrap();
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
                    .map(|b| BTreeMap::from([("first".into(), b)]))
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
        let agent = BTreeMap::from([("first".into(), true)]);
        let session = BTreeMap::from([("first".into(), false)]);
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
}
