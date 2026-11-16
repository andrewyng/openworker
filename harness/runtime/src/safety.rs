//! Optional guardrail engine and persistent configuration contract.
pub use openworker_guardrails::*;

/// Called only after cancellation/recovery or admission of a new turn. Receipt
/// proves execution; discarded candidate bytes cannot be recovered by replaying it.
pub fn reconcile_pending_result(s: &mut crate::store::Session) {
    if let Some(receipt) = s.turn.as_mut().and_then(|t| t.pending_safety_result.take()) {
        s.history.push(serde_json::json!({"type":"function_call_output","call_id":receipt["call_id"],"output":serde_json::json!({"executed":true,"outputBlocked":true,"code":"INSPECTION_INTERRUPTED","message":"The operation completed, but its result was not released. Do not repeat the action."}).to_string()}));
    }
}

/// Internal transactional tools stage only an execution receipt while their
/// result is inspected asynchronously. Candidate bytes never enter this receipt.
pub fn hold_internal_result(s: &mut crate::store::Session, call: &serde_json::Value) -> bool {
    let name = call["name"].as_str().unwrap_or_default();
    let Some(turn) = s.turn.as_mut() else {
        return false;
    };
    if !s.safety.protects(Phase::ToolResult, Some(name))
        && !turn.safety.protects(Phase::ToolResult, Some(name))
    {
        return false;
    }
    turn.pending_safety_result = Some(serde_json::json!({"call_id":call["call_id"],"name":name}));
    turn.queue.remove(0);
    turn.status = "checking_result".into();
    true
}

pub fn public_internal_event(
    s: &crate::store::Session,
    mut data: serde_json::Value,
) -> serde_json::Value {
    if let Some(receipt) = s
        .turn
        .as_ref()
        .and_then(|t| t.pending_safety_result.as_ref())
    {
        if data["call_id"] == receipt["call_id"] {
            if let Some(object) = data.as_object_mut() {
                object.remove("result");
                object.remove("plan");
            }
            data["result_held"] = serde_json::json!(true);
            data["executed"] = serde_json::json!(true);
        }
    }
    data
}
impl From<crate::connections::Choice> for ModelRef {
    fn from(value: crate::connections::Choice) -> Self {
        Self {
            connection: value.connection,
            model: value.model,
        }
    }
}
impl From<ModelRef> for crate::connections::Choice {
    fn from(value: ModelRef) -> Self {
        Self {
            connection: value.connection,
            model: value.model,
        }
    }
}
