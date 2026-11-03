//! App-owned content checks. Configuration is data, never model-provided authority.
//! Findings contain no original content; callback context and replacement
//! decisions are private candidates. Runtime adapters must gate
//! admission, delivery and side effects before publishing protected values.
#[cfg(feature = "engine")]
mod detectors;
#[cfg(feature = "engine")]
mod engine;
use anyhow::{ensure, Result};
#[cfg(feature = "engine")]
pub use detectors::fragments;
#[cfg(feature = "engine")]
pub use engine::{apply, observe, Evaluation};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    future::Future,
    pin::Pin,
    sync::{Arc, RwLock},
};
use tokio_util::sync::CancellationToken;

pub const MAX_TEXT: usize = 128_000;
pub const MAX_MATCHES: usize = 256;
pub const MAX_RULES: usize = 32;
fn version() -> String {
    "1".into()
}
fn timeout() -> u64 {
    10_000
}
fn replacement() -> String {
    "[redacted]".into()
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HandlerRef {
    pub name: String,
    #[serde(default = "version")]
    pub version: String,
}
impl HandlerRef {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            identifier(&self.name) && identifier(&self.version),
            "Invalid safety handler identity"
        );
        Ok(())
    }
}
fn identifier(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s.bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum Detector {
    CreditCard,
    Secrets,
    Regex {
        pattern: String,
        #[serde(default)]
        case_insensitive: bool,
    },
    Model {
        model: ModelRef,
        instructions: String,
    },
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum Action {
    Block,
    Redact {
        #[serde(default = "replacement")]
        replacement: String,
        #[serde(default)]
        keep_last: usize,
    },
    Callback {
        handler: HandlerRef,
    },
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Rule {
    pub name: String,
    #[serde(default = "version")]
    pub version: String,
    pub detector: Detector,
    pub action: Action,
    #[serde(default = "timeout")]
    pub timeout_ms: u64,
}
impl Rule {
    pub fn credit_card(action: Action) -> Self {
        Self {
            name: "builtin.credit-card".into(),
            version: version(),
            detector: Detector::CreditCard,
            action,
            timeout_ms: timeout(),
        }
    }
    pub fn secrets(action: Action) -> Self {
        Self {
            name: "builtin.secrets".into(),
            version: version(),
            detector: Detector::Secrets,
            action,
            timeout_ms: timeout(),
        }
    }
    pub fn regex(name: impl Into<String>, pattern: impl Into<String>, action: Action) -> Self {
        Self {
            name: name.into(),
            version: version(),
            detector: Detector::Regex {
                pattern: pattern.into(),
                case_insensitive: false,
            },
            action,
            timeout_ms: timeout(),
        }
    }
    pub fn validate(&self) -> Result<()> {
        ensure!(
            available(),
            "Guardrails capability is not included in this build"
        );
        ensure!(
            identifier(&self.name) && identifier(&self.version),
            "Invalid safety rule identity"
        );
        ensure!(
            (1..=120_000).contains(&self.timeout_ms),
            "Safety check timeout must be 1..120000 milliseconds"
        );
        match &self.detector {
            Detector::Regex {
                pattern,
                case_insensitive,
            } => {
                #[cfg(feature = "engine")]
                {
                    let re = detectors::regex(pattern, *case_insensitive)?;
                    ensure!(!re.is_match(""), "Safety regex must not match empty text");
                }
                #[cfg(not(feature = "engine"))]
                let _ = (pattern, case_insensitive);
            }
            Detector::Model {
                model,
                instructions,
            } => {
                model.validate()?;
                ensure!(
                    !instructions.trim().is_empty() && instructions.len() <= 8000,
                    "Invalid guard model instructions"
                );
            }
            _ => {}
        }
        match &self.action {
            Action::Redact {
                replacement,
                keep_last,
            } => ensure!(
                replacement.len() <= 512 && *keep_last <= 64,
                "Invalid redaction options"
            ),
            Action::Callback { handler } => handler.validate()?,
            _ => {}
        }
        Ok(())
    }
}
impl Action {
    pub fn redact() -> Self {
        Self::Redact {
            replacement: replacement(),
            keep_last: 0,
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum Phase {
    UserInput,
    ModelInput,
    ModelOutput,
    ToolCall,
    ToolResult,
    Event,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum HookPhase {
    BeforeModel,
    BeforeTool,
    AfterTool,
    BeforeOutput,
    OnEvent,
}
impl HookPhase {
    pub fn applies(self, p: Phase) -> bool {
        matches!(
            (self, p),
            (Self::BeforeModel, Phase::ModelInput)
                | (Self::BeforeTool, Phase::ToolCall)
                | (Self::AfterTool, Phase::ToolResult)
                | (Self::BeforeOutput, Phase::ModelOutput)
                | (Self::OnEvent, Phase::Event)
        )
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Hook {
    pub name: String,
    #[serde(default = "version")]
    pub version: String,
    pub when: HookPhase,
    #[serde(default)]
    pub tools: Vec<String>,
    pub handler: HandlerRef,
    #[serde(default = "timeout")]
    pub timeout_ms: u64,
}
impl Hook {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            available(),
            "Hooks capability is not included in this build"
        );
        ensure!(
            identifier(&self.name) && identifier(&self.version),
            "Invalid hook identity"
        );
        self.handler.validate()?;
        ensure!(
            (1..=120_000).contains(&self.timeout_ms),
            "Invalid hook timeout"
        );
        validate_tools(&self.tools)
    }
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ToolRules {
    pub tools: Vec<String>,
    pub rules: Vec<Rule>,
}
fn validate_tools(tools: &[String]) -> Result<()> {
    ensure!(tools.len() <= 64, "Too many safety tool selectors");
    for name in tools {
        openworker_tools::qualified_parts(name)?;
    }
    Ok(())
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Guardrails {
    #[serde(default)]
    pub user_input: Vec<Rule>,
    #[serde(default)]
    pub model_output: Vec<Rule>,
    #[serde(default)]
    pub tool_calls: Vec<ToolRules>,
    #[serde(default)]
    pub tool_results: Vec<ToolRules>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BlockedOutputOptions {
    pub max_retries: u32,
}
impl Default for BlockedOutputOptions {
    fn default() -> Self {
        Self { max_retries: 1 }
    }
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Config {
    #[serde(default)]
    pub guardrails: Guardrails,
    #[serde(default)]
    pub hooks: Vec<Hook>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub blocked_output_options: Option<BlockedOutputOptions>,
}
impl Config {
    pub fn validate(&self) -> Result<()> {
        for list in [&self.guardrails.user_input, &self.guardrails.model_output] {
            validate_rules(list)?;
        }
        for list in [&self.guardrails.tool_calls, &self.guardrails.tool_results] {
            ensure!(list.len() <= 32, "Too many tool safety selections");
            for group in list {
                ensure!(
                    !group.tools.is_empty(),
                    "Tool safety selection requires tool names"
                );
                validate_tools(&group.tools)?;
                validate_rules(&group.rules)?;
            }
        }
        ensure!(self.hooks.len() <= 32, "Too many hooks");
        let mut seen = BTreeMap::new();
        for hook in &self.hooks {
            hook.validate()?;
            if let Some(old) = seen.insert(&hook.name, hook) {
                ensure!(old == hook, "Conflicting hook definitions");
            }
        }
        if let Some(o) = &self.blocked_output_options {
            ensure!(o.max_retries <= 3, "At most three output retries allowed");
        }
        Ok(())
    }
    pub fn rules(&self, phase: Phase, tool: Option<&str>) -> Vec<&Rule> {
        match phase {
            Phase::UserInput => self.guardrails.user_input.iter().collect(),
            Phase::ModelOutput => self.guardrails.model_output.iter().collect(),
            Phase::ToolCall | Phase::ToolResult => {
                let groups = if phase == Phase::ToolCall {
                    &self.guardrails.tool_calls
                } else {
                    &self.guardrails.tool_results
                };
                groups
                    .iter()
                    .filter(|g| tool.is_some_and(|t| g.tools.iter().any(|v| v == t)))
                    .flat_map(|g| g.rules.iter())
                    .collect()
            }
            _ => vec![],
        }
    }
    pub fn protects(&self, phase: Phase, tool: Option<&str>) -> bool {
        !self.rules(phase, tool).is_empty()
            || self.hooks.iter().any(|h| {
                h.when != HookPhase::OnEvent
                    && h.when.applies(phase)
                    && (h.tools.is_empty() || tool.is_some_and(|t| h.tools.iter().any(|v| v == t)))
            })
    }
    pub fn merge(&self, additional: &Self) -> Result<Self> {
        self.validate()?;
        additional.validate()?;
        let mut out = self.clone();
        extend(
            &mut out.guardrails.user_input,
            &additional.guardrails.user_input,
        )?;
        extend(
            &mut out.guardrails.model_output,
            &additional.guardrails.model_output,
        )?;
        for (target, source) in [
            (
                &mut out.guardrails.tool_calls,
                &additional.guardrails.tool_calls,
            ),
            (
                &mut out.guardrails.tool_results,
                &additional.guardrails.tool_results,
            ),
        ] {
            for group in source {
                if let Some(existing) = target.iter_mut().find(|g| g.tools == group.tools) {
                    extend(&mut existing.rules, &group.rules)?;
                } else {
                    target.push(group.clone());
                }
            }
        }
        for h in &additional.hooks {
            if let Some(old) = out.hooks.iter().find(|v| v.name == h.name) {
                ensure!(old == h, "Cannot replace an inherited hook");
            } else {
                out.hooks.push(h.clone());
            }
        }
        if let Some(o) = &additional.blocked_output_options {
            if let Some(max) = &self.blocked_output_options {
                ensure!(
                    o.max_retries <= max.max_retries,
                    "Cannot increase inherited output retries"
                );
            }
            out.blocked_output_options = Some(o.clone());
        }
        out.validate()?;
        Ok(out)
    }
}
fn validate_rules(rules: &[Rule]) -> Result<()> {
    ensure!(rules.len() <= MAX_RULES, "Too many safety rules");
    let mut seen = BTreeMap::new();
    for r in rules {
        r.validate()?;
        if let Some(old) = seen.insert(&r.name, r) {
            ensure!(old == r, "Conflicting safety rule definitions");
        }
    }
    Ok(())
}
fn extend(target: &mut Vec<Rule>, source: &[Rule]) -> Result<()> {
    for rule in source {
        if let Some(old) = target.iter().find(|r| r.name == rule.name) {
            ensure!(old == rule, "Cannot replace an inherited guardrail");
        } else {
            target.push(rule.clone());
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Context {
    pub evaluation_id: String,
    pub session_id: String,
    pub turn_id: Option<String>,
    pub phase: Phase,
    pub text: String,
    pub tool: Option<String>,
    pub arguments: Option<Value>,
    #[serde(default)]
    pub matches: Vec<Match>,
    #[serde(default)]
    pub context: Value,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(
    tag = "action",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum Decision {
    Allow,
    Block {
        code: String,
        #[serde(default)]
        feedback_to_model: String,
    },
    Replace {
        text: String,
    },
}
impl Decision {
    pub fn block(code: impl Into<String>) -> Self {
        Self::Block{code:code.into(),feedback_to_model:"This action or content is blocked by application policy. Choose an allowed alternative without sensitive information.".into()}
    }
    pub fn validate(&self) -> Result<()> {
        match self {
            Self::Allow => {}
            Self::Block {
                code,
                feedback_to_model,
            } => {
                ensure!(
                    identifier(code) && feedback_to_model.len() <= 2000,
                    "Invalid safety block response"
                );
            }
            Self::Replace { text } => ensure!(
                text.len() <= MAX_TEXT,
                "Replacement exceeds safety size limit"
            ),
        };
        Ok(())
    }
}
pub type CheckFuture<'a> = Pin<Box<dyn Future<Output = Result<Decision>> + Send + 'a>>;
pub trait Handler: Send + Sync {
    fn call(&self, context: Context, cancellation: CancellationToken) -> CheckFuture<'_>;
}
pub type DetectFuture<'a> = Pin<Box<dyn Future<Output = Result<Vec<String>>> + Send + 'a>>;
pub trait ModelDetector: Send + Sync {
    fn detect(
        &self,
        model: ModelRef,
        instructions: String,
        context: Context,
        cancellation: CancellationToken,
    ) -> DetectFuture<'_>;
}
#[derive(Default)]
pub struct Registry {
    handlers: RwLock<BTreeMap<(String, String), Arc<dyn Handler>>>,
}
impl Registry {
    pub fn register(&self, id: HandlerRef, handler: Arc<dyn Handler>) -> Result<()> {
        id.validate()?;
        let mut h = self.handlers.write().unwrap();
        ensure!(
            !h.contains_key(&(id.name.clone(), id.version.clone())),
            "Safety handler already registered"
        );
        h.insert((id.name, id.version), handler);
        Ok(())
    }
    pub fn handler(&self, id: &HandlerRef) -> Result<Arc<dyn Handler>> {
        self.handlers
            .read()
            .unwrap()
            .get(&(id.name.clone(), id.version.clone()))
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("Required safety handler unavailable"))
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Finding {
    pub evaluation_id: String,
    pub phase: Phase,
    pub check: String,
    pub version: String,
    pub outcome: String,
    pub code: Option<String>,
    pub match_count: usize,
    pub duration_ms: u64,
}
#[cfg(feature = "engine")]
fn unavailable(context: &Context, name: &str, version: &str) -> Finding {
    Finding {
        evaluation_id: context.evaluation_id.clone(),
        phase: context.phase,
        check: name.into(),
        version: version.into(),
        outcome: "unavailable".into(),
        code: Some("GUARDRAIL_UNAVAILABLE".into()),
        match_count: 0,
        duration_ms: 0,
    }
}

#[cfg(all(test, feature = "engine"))]
mod tests;

/// Build capability, independent of whether an application has enabled checks.
pub const fn available() -> bool {
    cfg!(feature = "engine")
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ModelRef {
    pub connection: String,
    pub model: String,
}
impl ModelRef {
    pub fn new(connection: impl Into<String>, model: impl Into<String>) -> Self {
        Self {
            connection: connection.into(),
            model: model.into(),
        }
    }
    pub fn validate(&self) -> Result<()> {
        ensure!(
            identifier(&self.connection)
                && self.connection.len() <= 64
                && !self.model.is_empty()
                && self.model.len() <= 256
                && self
                    .model
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"-._:/".contains(&c)),
            "Invalid guard model reference"
        );
        Ok(())
    }
}
/// UTF-8 byte offsets in the exact checked text; SDKs must not use these as
/// JavaScript string indices. Matched text is private callback input, not audit data.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Match {
    pub start: usize,
    pub end: usize,
    pub text: String,
}

#[cfg(all(test, not(feature = "engine")))]
mod minimal_tests {
    use super::*;
    #[test]
    fn absent_engine_rejects_required_checks() {
        assert!(!available());
        Config::default().validate().unwrap();
        assert!(Rule::secrets(Action::Block).validate().is_err());
        let h = Hook {
            name: "test".into(),
            version: "1".into(),
            when: HookPhase::BeforeTool,
            tools: vec![],
            handler: HandlerRef {
                name: "test".into(),
                version: "1".into(),
            },
            timeout_ms: 10,
        };
        assert!(h.validate().is_err());
    }
}
