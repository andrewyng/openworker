//! Retirement is a durable admission gate, separate from cancelling one turn.
//! Cleanup uses the owning runtime; this module never deletes an environment.
use crate::{
    agent_team::{Document, Transaction},
    store::Session,
    team_coordination::Phase,
};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    #[default]
    FinishCurrent,
    Cancel,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Retirement {
    pub mode: Mode,
    pub requested_at_ms: u64,
    pub settled_at_ms: Option<u64>,
    #[serde(default)]
    pub pending_reason: Option<String>,
    /// Original turn may finish already accepted async handoffs; retirement
    /// never admits a later turn or resurrects a cancelled effect.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub turn_id: Option<String>,
}
fn limit() -> usize {
    50
}
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    List {
        #[serde(default)]
        after: Option<String>,
        #[serde(default = "limit")]
        limit: usize,
    },
    Retire {
        agent_id: String,
        #[serde(default)]
        mode: Mode,
    },
}
pub fn apply(
    tx: &mut dyn Transaction,
    doc: &mut Document,
    caller: &str,
    args: &Value,
    now: u64,
) -> Result<Value> {
    ensure!(caller == doc.lead, "Only the lead manages team membership");
    match serde_json::from_value::<Request>(args.clone())? {
        Request::List { after, limit } => {
            ensure!(
                (1..=100).contains(&limit),
                "Roster page requires 1..100 entries"
            );
            if let Some(id) = &after {
                ensure!(doc.roster.instance(id).is_some(), "Unknown roster cursor");
            }
            let rows: Vec<_> = doc
                .roster
                .instances()
                .filter(|i| after.as_ref().is_none_or(|id| i.agent_id > *id))
                .take(limit + 1)
                .collect();
            let more = rows.len() > limit;
            let mut members = vec![];
            for i in rows.into_iter().take(limit) {
                let s = tx.optional_session(&i.session_id)?;
                members.push(json!({"instance":i,"title":doc.roster.request(&i.agent_id).and_then(|r|r.title.as_ref()),"type_revision":doc.type_bindings.get(&i.agent_id),"retirement":doc.retirements.get(&i.agent_id),"error":doc.failures.get(&i.agent_id),
                    "workspace":s.as_ref().map(|s|&s.workspace),
                    "last_outcome":s.as_ref().and_then(|s|s.turn.as_ref()).map(|t|&t.status),
                    "outcome_unknown":s.as_ref().and_then(|s|s.turn.as_ref()).is_some_and(|t|t.status=="interrupted") || doc.workspaces.get(&i.agent_id).is_some_and(|w|w.error.is_some()),
                    "model":s.as_ref().map(|s|json!({"connection":s.model_selection.as_ref().map(|m|&m.connection),"model":s.model})),
                    "environment":s.as_ref().and_then(|s|s.environment.as_ref()).map(|e|json!({"id":e.environment_id,"provider":e.spec.provider,"remote_id":e.remote_id}))
                }));
            }
            let next = more.then(|| members.last().unwrap()["instance"]["agent_id"].clone());
            Ok(json!({"members":members,"next":next}))
        }
        Request::Retire { agent_id, mode } => {
            ensure!(agent_id != doc.lead, "Stop the team to retire its lead");
            let member = doc.roster.instance(&agent_id).context("Unknown instance")?;
            if let Some(saved) = doc.retirements.get(&agent_id) {
                ensure!(saved.mode==mode,"Retirement mode already pinned; use the normal turn cancellation API to interrupt current work");
            } else {
                ensure!(member.phase != Phase::Closed, "Instance already closed");
                let turn_id = tx
                    .optional_session(&member.session_id)?
                    .and_then(|s| s.turn.map(|t| t.id));
                doc.roster.retire(&agent_id)?;
                doc.retirements.insert(
                    agent_id.clone(),
                    Retirement {
                        mode,
                        requested_at_ms: now,
                        settled_at_ms: None,
                        pending_reason: None,
                        turn_id,
                    },
                );
            }
            Ok(
                json!({"instance":doc.roster.instance(&agent_id),"retirement":doc.retirements.get(&agent_id)}),
            )
        }
    }
}
/// The runtime must have finished cleanup and persisted session closure before
/// releasing capacity. An unprovisioned reservation has no session to close.
pub fn settle(tx: &mut dyn Transaction, doc: &mut Document, id: &str, now: u64) -> Result<Value> {
    let member = doc.roster.instance(id).context("Unknown instance")?;
    let retirement = doc.retirements.get(id).context("No retirement request")?;
    if retirement.settled_at_ms.is_some() {
        return Ok(json!({"settled":true,"agent_id":id}));
    }
    ensure!(member.phase == Phase::Retiring, "Instance is not retiring");
    if let Some(s) = tx.optional_session(&member.session_id)? {
        ensure!(
            s.closed
                && s.turn
                    .as_ref()
                    .is_none_or(|t| crate::store::terminal(&t.status)),
            "Member work/cleanup has not settled"
        );
    }
    doc.roster.close(id)?;
    let retirement = doc.retirements.get_mut(id).unwrap();
    retirement.settled_at_ms = Some(now);
    retirement.pending_reason = None;
    Ok(json!({"settled":true,"agent_id":id}))
}
pub fn admits(doc: &Document, s: &Session) -> bool {
    !doc.stopped
        && s.agent_team
            .as_ref()
            .and_then(|m| doc.roster.instance(&m.agent_id))
            .is_some_and(|i| i.session_id == s.id && i.phase == Phase::Ready)
}
/// Continuation of an already approved durable operation, never admission for a
/// new tool action. Other profile/credential/cancellation fences still apply.
pub fn accepted_effect(doc: &Document, agent: &str, turn: &str) -> bool {
    doc.roster
        .instance(agent)
        .is_some_and(|i| i.phase == Phase::Ready)
        || doc
            .retirements
            .get(agent)
            .is_some_and(|r| r.mode == Mode::FinishCurrent && r.turn_id.as_deref() == Some(turn))
}
pub fn schema() -> Value {
    json!({"action":{"type":"string","enum":["list","retire"]},"agent_id":{"type":"string"},"mode":{"type":"string","enum":["finish_current","cancel"]},"after":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100}})
}
