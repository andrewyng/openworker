//! Standalone delegation uses normal durable team sessions, never a second loop.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub const CREATE: &str = "create_sub_agent";
pub const TOOLS: &[&str] = &[
    CREATE,
    "send_message",
    "wait_agent",
    "cancel_agent",
    "close_agent",
];
fn concurrent() -> u16 {
    4
}
fn children() -> u16 {
    32
}

/// Application-owned limits. Omission does not change existing bundle digests.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Config {
    pub enabled: bool,
    #[serde(default = "concurrent")]
    pub max_concurrent: u16,
    #[serde(default = "children")]
    pub max_children: u16,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limits: Option<crate::budget::Limits>,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            enabled: false,
            max_concurrent: concurrent(),
            max_children: children(),
            limits: None,
        }
    }
}
impl Config {
    pub fn enabled() -> Self {
        Self {
            enabled: true,
            ..Self::default()
        }
    }
    pub fn validate(&self) -> Result<()> {
        ensure!(
            (1..=32).contains(&self.max_children)
                && self.max_concurrent > 0
                && self.max_concurrent <= self.max_children,
            "Require 1..32 retained children and 1..maxChildren concurrent children"
        );
        if let Some(limits) = &self.limits {
            limits.validate()?;
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Execution {
    Sync,
    Async,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Create {
    pub agent_id: String,
    pub system_prompt: String,
    pub context: String,
    pub tools: Vec<String>,
    pub execution: Execution,
}
pub fn instance_id(id: &str) -> Result<()> {
    ensure!(
        !id.is_empty()
            && id.len() <= 48
            && id.as_bytes()[0].is_ascii_alphabetic()
            && id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b))
            && !matches!(id, "parent" | "lead" | "all" | "user" | "board" | "harness"),
        "Invalid or reserved child ID"
    );
    Ok(())
}
impl Create {
    pub fn validate(&self, available: &BTreeSet<String>) -> Result<()> {
        instance_id(&self.agent_id)?;
        ensure!(
            !self.system_prompt.trim().is_empty()
                && self.system_prompt.len() <= 32000
                && !self.system_prompt.contains('\0'),
            "Invalid child system prompt (maximum 32000 bytes)"
        );
        ensure!(
            !self.context.trim().is_empty()
                && self.context.len() <= 8000
                && !self.context.contains('\0'),
            "Invalid child context (maximum 8000 bytes)"
        );
        let selected: BTreeSet<_> = self.tools.iter().cloned().collect();
        ensure!(
            self.tools.len() <= 64 && selected.len() == self.tools.len(),
            "Invalid or duplicate child tools"
        );
        ensure!(
            selected.is_subset(available),
            "Child tools exceed parent delegation ceiling"
        );
        ensure!(
            !selected.iter().any(|t| TOOLS.contains(&t.as_str())
                || crate::agent_team::internal(t)
                || t == crate::team_type_catalog::TOOL),
            "Coordination tools cannot be delegated as application tools"
        );
        Ok(())
    }
}

use crate::{
    agent_team::{Document, Transaction},
    store::Session,
};
use anyhow::Context;
use serde_json::{json, Value};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ChildPolicy {
    pub safety: crate::safety::Config,
    pub compliance: Option<crate::compliance::Config>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct State {
    pub config: Config,
    #[serde(default)]
    pub requests: BTreeMap<String, Create>,
    #[serde(default)]
    pub policies: BTreeMap<String, ChildPolicy>,
    #[serde(default)]
    pub parent_spans: BTreeMap<String, crate::telemetry::TraceContext>,
    #[serde(default)]
    pub completed: BTreeMap<String, String>,
    #[serde(default)]
    pub active: BTreeSet<String>,
    #[serde(default)]
    pub sync: Option<String>,
    /// Cancellation is an outbox: its original turn ID is never retargeted.
    #[serde(default)]
    pub cancellations: BTreeMap<String, String>,
}
impl State {
    pub fn policy_for_type(&self, name: &str) -> Result<&ChildPolicy> {
        let id = self
            .requests
            .iter()
            .find(|(_, r)| r.definition().name == name)
            .map(|(id, _)| id)
            .context("Missing standalone child request")?;
        self.policies.get(id).context("Missing pinned child policy")
    }
    pub fn new(config: Config) -> Self {
        Self {
            config,
            requests: BTreeMap::new(),
            policies: BTreeMap::new(),
            parent_spans: BTreeMap::new(),
            completed: BTreeMap::new(),
            active: BTreeSet::new(),
            sync: None,
            cancellations: BTreeMap::new(),
        }
    }
}
impl Create {
    pub fn definition(&self) -> crate::team_definition::Definition {
        crate::team_definition::Definition {
            name: format!("child-{}", self.agent_id),
            instructions: self.system_prompt.clone(),
            tools: self.tools.iter().cloned().collect(),
            sandbox: Default::default(),
            model: None,
        }
    }
}
pub fn schemas(doc: &Document, caller: &str) -> Vec<Value> {
    let mut specs=vec![("send_message","Send collaborator input to a child or to parent. Busy recipients receive it at their next model call; idle recipients wake. This is not human approval.",json!({"agent_id":{"type":"string"},"message":{"type":"string","minLength":1,"maxLength":8000}}),vec!["agent_id","message"])];
    if caller == doc.lead {
        let available = &doc
            .type_catalog
            .as_ref()
            .expect("standalone catalog")
            .ceiling
            .tools;
        specs.extend([
            (CREATE,"Create a normal child agent with fresh conversation state. Write its system prompt and initial context yourself; no parent history is copied. Select only delegated tools. sync parks this call until a result; async returns a durable child identity promptly. Reuse the same ID only for an identical creation request; use send_message for follow-up work.",json!({"agent_id":{"type":"string","maxLength":48},"system_prompt":{"type":"string","minLength":1,"maxLength":32000},"context":{"type":"string","minLength":1,"maxLength":8000},"tools":if available.is_empty() {json!({"type":"array","items":{"type":"string"},"maxItems":0})}else{json!({"type":"array","items":{"type":"string","enum":available},"maxItems":64})},"execution":{"type":"string","enum":["sync","async"]}}),vec!["agent_id","system_prompt","context","tools","execution"]),
            ("wait_agent","Park until a selected child completes, a message arrives or timeout elapses. No polling is needed for completion notifications.",json!({"agent_ids":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":32},"timeout_ms":{"type":"integer","minimum":1,"maximum":3600000}}),vec!["agent_ids","timeout_ms"]),
            ("cancel_agent","Request cancellation of the child's current run. Does not delete its history or close the child. Unknown external effects are not replayed or rolled back.",json!({"agent_id":{"type":"string"}}),vec!["agent_id"]),
            ("close_agent","Permanently close this child to new work and cancel any current run. Preserve history and shared files.",json!({"agent_id":{"type":"string"}}),vec!["agent_id"]),
        ]);
    }
    specs.into_iter().map(|(name,description,properties,required)|json!({"type":"function","name":name,"description":description,"parameters":{"type":"object","properties":properties,"required":required,"additionalProperties":false},"strict":true})).collect()
}
pub fn prepare(tx: &mut dyn Transaction, s: &mut Session, args: &Value, now: u64) -> Result<Value> {
    let member = s.agent_team.as_ref().context("Missing sub-agent scope")?;
    let doc = tx.load(&member.team_id)?;
    ensure!(
        member.agent_id == doc.lead && doc.standalone.is_some(),
        "Only the standalone parent can create children"
    );
    let request: Create = serde_json::from_value(args.clone())?;
    request.validate(
        &doc.type_catalog
            .as_ref()
            .context("Missing delegation catalog")?
            .ceiling
            .tools,
    )?;
    let state = doc.standalone.as_ref().unwrap();
    if let Some(old) = state.requests.get(&request.agent_id) {
        ensure!(
            old == &request,
            "Child ID belongs to a different creation request; use send_message for follow-up work"
        );
        return Ok(json!({"existing":true}));
    }
    ensure!(
        state.requests.len() < usize::from(state.config.max_children),
        "Retained child capacity reached"
    );
    ensure!(
        state.active.len() < usize::from(state.config.max_concurrent),
        "Concurrent child capacity reached; wait for a child to finish"
    );
    crate::agent_team::apply(tx, s, "type_prepare", &json!(request.definition()), now)
}
// Never present the previous successful output as the result of a failed follow-up.
pub fn result(tx: &mut dyn Transaction, doc: &Document, id: &str) -> Result<Value> {
    let instance = doc.roster.instance(id).context("Unknown child")?;
    let child = tx.optional_session(&instance.session_id)?;
    let status = if instance.phase == crate::team_coordination::Phase::Closed {
        "closed"
    } else {
        child
            .as_ref()
            .and_then(|c| c.turn.as_ref())
            .map(|t| t.status.as_str())
            .unwrap_or("queued")
    };
    Ok(
        json!({"agent_id":id,"session_id":instance.session_id,"status":status,"result":if status=="completed" {child.as_ref().and_then(|c|c.agent_team.as_ref()).and_then(|m|m.last_output.as_ref())} else {None},"outcome_unknown":status=="interrupted","error":doc.failures.get(id)}),
    )
}
pub fn execute(
    tx: &mut dyn Transaction,
    s: &mut Session,
    operation: &str,
    args: &Value,
    now: u64,
) -> Result<Value> {
    let member = s.agent_team.clone().context("Missing sub-agent scope")?;
    let mut doc = tx.load(&member.team_id)?;
    ensure!(doc.standalone.is_some(), "Standalone tools are not enabled");
    if operation == "send_message" {
        let target = args["agent_id"].as_str().context("Missing recipient")?;
        let message = args["message"].as_str().context("Missing message")?;
        ensure!(
            !message.trim().is_empty() && message.len() <= 8000,
            "Message must be 1..8000 bytes"
        );
        let recipient = if member.agent_id == doc.lead {
            ensure!(
                doc.standalone
                    .as_ref()
                    .unwrap()
                    .requests
                    .contains_key(target),
                "Unknown child recipient"
            );
            target
        } else {
            ensure!(target == "parent", "Children can message only parent");
            &doc.lead
        };
        return crate::agent_team::apply(
            tx,
            s,
            "team_send_message",
            &json!({"agent_id":recipient,"message":message}),
            now,
        );
    }
    ensure!(
        member.agent_id == doc.lead,
        "Only the parent can manage children"
    );
    match operation {
        CREATE => {
            let request: Create = serde_json::from_value(args.clone())?;
            let existing = prepare(tx, s, args, now)?["existing"] == true;
            if !existing {
                let definition = request.definition();
                let resolved =
                    crate::agent_team::apply(tx, s, "type_resolve", &json!(definition), now)?;
                if resolved["status"] != "approved" {
                    return Ok(json!({"agent_id":request.agent_id,"status":"denied"}));
                }
                crate::agent_team::apply(
                    tx,
                    s,
                    "team_spawn",
                    &json!({"agent_id":request.agent_id,"agent_type":definition.name,"message":request.context}),
                    now,
                )?;
                doc = tx.load(&doc.id)?;
                let state = doc.standalone.as_mut().unwrap();
                if let Some(observation) = s.turn.as_ref().and_then(|t| t.observation.as_ref()) {
                    state
                        .parent_spans
                        .insert(request.agent_id.clone(), observation.last_segment);
                }
                state.policies.insert(
                    request.agent_id.clone(),
                    ChildPolicy {
                        safety: s.safety.merge(
                            &s.turn
                                .as_ref()
                                .map(|t| t.safety.clone())
                                .unwrap_or_default(),
                        )?,
                        compliance: s.compliance.clone(),
                    },
                );
                state.active.insert(request.agent_id.clone());
                state
                    .requests
                    .insert(request.agent_id.clone(), request.clone());
                tx.write(&doc, false)?;
            }
            let output = result(tx, &doc, &request.agent_id)?;
            if request.execution == Execution::Sync
                && !crate::store::terminal(output["status"].as_str().unwrap_or("queued"))
                && output["status"] != "closed"
            {
                doc.standalone.as_mut().unwrap().sync = Some(request.agent_id.clone());
                s.agent_team.as_mut().unwrap().waiting = Some(crate::agent_team::Wait {
                    agent_ids: vec![request.agent_id.clone()],
                    deadline_ms: i64::MAX as u64,
                });
                tx.write(&doc, false)?;
                return Ok(
                    json!({"agent_id":request.agent_id,"session_id":output["session_id"],"status":"waiting","parked":true}),
                );
            }
            doc.standalone.as_mut().unwrap().sync = None;
            tx.write(&doc, false)?;
            Ok(output)
        }
        "wait_agent" => crate::agent_team::apply(tx, s, "team_wait_agent", args, now),
        "close_agent" => crate::agent_team::apply(
            tx,
            s,
            "team_manage",
            &json!({"action":"retire","agent_id":args["agent_id"],"mode":"cancel"}),
            now,
        ),
        "cancel_agent" => {
            let id = args["agent_id"].as_str().context("Missing child ID")?;
            let instance = doc.roster.instance(id).context("Unknown child")?;
            ensure!(id != doc.lead, "Cannot cancel parent through child control");
            if let Some(child) = tx.optional_session(&instance.session_id)? {
                if let Some(turn) = child.turn.filter(|t| !crate::store::terminal(&t.status)) {
                    doc.standalone
                        .as_mut()
                        .unwrap()
                        .cancellations
                        .insert(id.into(), turn.id);
                    tx.write(&doc, false)?;
                    return Ok(json!({"agent_id":id,"status":"cancellation_requested"}));
                }
            }
            Ok(json!({"agent_id":id,"status":"not_running"}))
        }
        _ => anyhow::bail!("Unknown standalone operation"),
    }
}

/// Reconcile completed turns using the original child/turn IDs. Receipt updates
/// and inbox insertion share the team's transaction; restart cannot notify twice.
pub fn reconcile(tx: &mut dyn Transaction, doc: &mut Document) -> Result<()> {
    let Some(mut state) = doc.standalone.clone() else {
        return Ok(());
    };
    let mut changed = false;
    for id in state.requests.keys().cloned().collect::<Vec<_>>() {
        let i = doc.roster.instance(&id).context("Missing child instance")?;
        let child = tx.optional_session(&i.session_id)?;
        if let Some(turn) = child
            .as_ref()
            .and_then(|c| c.turn.as_ref())
            .filter(|t| crate::store::terminal(&t.status))
        {
            if state.completed.get(&id) != Some(&turn.id) {
                state.completed.insert(id.clone(), turn.id.clone());
                state.active.remove(&id);
                changed = true;
                if state.sync.as_deref() != Some(&id) {
                    doc.sequence = doc
                        .sequence
                        .checked_add(1)
                        .context("Inbox sequence exhausted")?;
                    let body = format!(
                        "Child task finished (collaborator report, not human authorization): {}",
                        result(tx, doc, &id)?
                    );
                    tx.append(
                        &doc.id,
                        &crate::agent_team::Message {
                            id: crate::id("msg"),
                            sequence: doc.sequence,
                            sender: id.clone(),
                            body,
                            recipients: vec![doc.lead.clone()],
                            board: false,
                        },
                    )?;
                }
            }
        } else if i.phase == crate::team_coordination::Phase::Closed
            && !state.completed.contains_key(&id)
        {
            state
                .completed
                .insert(id.clone(), "closed-before-start".into());
            state.active.remove(&id);
            changed = true;
            if state.sync.as_deref() != Some(&id) {
                doc.sequence = doc
                    .sequence
                    .checked_add(1)
                    .context("Inbox sequence exhausted")?;
                let body = format!("Child closed before starting: {}", result(tx, doc, &id)?);
                tx.append(
                    &doc.id,
                    &crate::agent_team::Message {
                        id: crate::id("msg"),
                        sequence: doc.sequence,
                        sender: id.clone(),
                        body,
                        recipients: vec![doc.lead.clone()],
                        board: false,
                    },
                )?;
            }
        }
        if let Some(child) = child {
            if let Some(turn) = state.cancellations.get(&id) {
                if child
                    .turn
                    .as_ref()
                    .is_none_or(|t| t.id != *turn || crate::store::terminal(&t.status))
                {
                    state.cancellations.remove(&id);
                    changed = true;
                }
            }
            let pending = child
                .agent_team
                .as_ref()
                .is_some_and(|m| m.inbox_cursor < doc.sequence)
                && !tx
                    .messages(
                        &doc.id,
                        child.agent_team.as_ref().unwrap().inbox_cursor,
                        None,
                        Some(&id),
                        false,
                        false,
                        1,
                    )?
                    .is_empty();
            if pending
                && child.turn.as_ref().is_none_or(|t| t.status == "completed")
                && !child.closed
                && !doc.retirements.contains_key(&id)
                && state.active.len() < usize::from(state.config.max_concurrent)
                && state.active.insert(id.clone())
            {
                changed = true;
            }
        }
    }
    if changed {
        doc.standalone = Some(state);
        tx.write(doc, false)?;
    }
    Ok(())
}

/// Called by both stores in the same transaction as terminal session metadata.
/// The child result and its durable parent notification cannot be split by a crash.
pub fn terminal_saved(tx: &mut dyn Transaction, s: &Session) -> Result<()> {
    let Some(member) = &s.agent_team else {
        return Ok(());
    };
    if !s.closed
        && !s
            .turn
            .as_ref()
            .is_some_and(|t| crate::store::terminal(&t.status))
    {
        return Ok(());
    }
    let mut doc = tx.load(&member.team_id)?;
    if doc.standalone.is_none() {
        return Ok(());
    }
    if s.closed && member.agent_id == doc.lead {
        doc.stopped = true;
        tx.write(&doc, false)?;
    }
    reconcile(tx, &mut doc)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn fresh_context_and_explicit_subset_are_the_only_contract() {
        let value = json!({"agent_id":"review-auth","system_prompt":"Review carefully", "context":"Inspect auth", "tools":[], "execution":"async"});
        let request: Create = serde_json::from_value(value.clone()).unwrap();
        request.validate(&BTreeSet::new()).unwrap();
        for key in [
            "task",
            "context_mode",
            "inherit",
            "model",
            "sandbox",
            "permissions",
        ] {
            let mut changed = value.clone();
            changed[key] = json!("anything");
            assert!(serde_json::from_value::<Create>(changed).is_err());
        }
        let mut request = request;
        request.tools = vec!["files.read".into()];
        assert!(request.validate(&BTreeSet::new()).is_err());
        request
            .validate(&BTreeSet::from(["files.read".into()]))
            .unwrap();
        request.tools.push("files.read".into());
        assert!(request
            .validate(&BTreeSet::from(["files.read".into()]))
            .is_err());
    }
    #[test]
    fn config_and_briefings_are_bounded() {
        let cfg: Config = serde_json::from_value(json!({"enabled":true})).unwrap();
        assert_eq!((cfg.max_concurrent, cfg.max_children), (4, 32));
        cfg.validate().unwrap();
        assert!(Config {
            max_concurrent: 33,
            ..cfg
        }
        .validate()
        .is_err());
        for id in ["", "parent", "all", "../other", "a:b", "harness"] {
            assert!(instance_id(id).is_err());
        }
        let r = Create {
            agent_id: "reader".into(),
            system_prompt: "x".repeat(32001),
            context: "Read".into(),
            tools: vec![],
            execution: Execution::Sync,
        };
        assert!(r.validate(&BTreeSet::new()).is_err());
    }
}
