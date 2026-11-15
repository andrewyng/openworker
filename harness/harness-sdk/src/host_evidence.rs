//! Opt-in, bounded projection of committed execution events. No attempt deltas,
//! provider-private reasoning, rejected candidates or unselected tool payloads.
use super::Host;
use anyhow::{ensure, Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Query {
    session_id: String,
    turn_id: String,
    #[serde(default)]
    after_event_id: i64,
}
fn redact(v: Value) -> Value {
    match v {
        Value::String(s) => json!(openworker_telemetry::redact(&s)),
        Value::Array(a) => Value::Array(a.into_iter().map(redact).collect()),
        Value::Object(o) => Value::Object(o.into_iter().map(|(k, v)| (k, redact(v))).collect()),
        v => v,
    }
}

fn project(kind: &str, data: &Value, policy: &crate::recording::EvidencePolicy) -> Option<Value> {
    match kind {
        "assistant_message" if policy.assistant_output => Some(
            json!({"messages":data["messages"].as_array().into_iter().flatten().map(|m|json!({"role":"assistant","content":m["content"].as_array().into_iter().flatten().filter(|p|p["type"]=="output_text").map(|p|json!({"type":"output_text","text":p["text"]})).collect::<Vec<_>>()})).collect::<Vec<_>>()}),
        ),
        "tool_started" if policy.tool_arguments => Some(
            json!({"name":data["name"],"call_id":data["call_id"],"arguments":data["arguments"]}),
        ),
        "tool_finished"
            if policy.tool_results
                && data["outputBlocked"] != true
                && data["result"]["outputBlocked"] != true =>
        {
            Some(
                json!({"name":data["name"],"call_id":data["call_id"],"result":data["result"],"executed":data["executed"]}),
            )
        }
        "turn_completed" | "turn_recovered" | "turn_failed" | "turn_cancelled"
        | "turn_interrupted" => Some(
            json!({"status":data["status"],"outcome_unknown_count":data["outcome_unknown_count"],"automatic_replay":data["automatic_replay"]}),
        ),
        _ => None,
    }
}
impl Host {
    pub(super) fn evidence_dispatch(&self, p: Value) -> Result<Value> {
        let q: Query = serde_json::from_value(p)?;
        crate::TraceQuery::turn(&q.session_id, &q.turn_id).validate()?;
        ensure!(q.after_event_id >= 0, "Invalid evidence cursor");
        let policy = self
            .config
            .recording
            .as_ref()
            .and_then(|r| r.evidence.as_ref())
            .context("Content evidence is disabled")?;
        let store = self.store.lock().unwrap();
        ensure!(
            store.has_turn(&q.session_id, &q.turn_id)?,
            "Evidence target turn unavailable"
        );
        let session = store.metadata(&q.session_id)?;
        let events = store.events(&q.session_id, q.after_event_id)?;
        let through = store.cursor(&q.session_id)?;
        let current = session.turn.as_ref().filter(|t| t.id == q.turn_id);
        let mut bytes = 0;
        let mut omitted = 0;
        let mut result = Vec::new();
        let input = if q.after_event_id == 0 && policy.user_input {
            current.map(|t| openworker_telemetry::redact(&t.text))
        } else {
            None
        };
        let input = input.filter(|text| {
            if text.len() <= policy.max_bytes {
                bytes += text.len();
                true
            } else {
                omitted += 1;
                false
            }
        });
        let mut after = q.after_event_id;
        for e in events {
            after = e.event_id;
            if e.turn_id.as_deref() != Some(&q.turn_id) {
                continue;
            }
            let payload = project(&e.kind, &e.data, policy);
            if let Some(payload) = payload {
                let projected = json!({"event_id":e.event_id,"kind":e.kind,"data":redact(payload)});
                let size = serde_json::to_vec(&projected)?.len();
                if bytes + size > policy.max_bytes {
                    omitted += 1;
                    continue;
                }
                bytes += size;
                result.push(projected);
            }
        }
        Ok(
            json!({"version":1,"session_id":q.session_id,"turn_id":q.turn_id,"turn_status":current.map(|t|t.status.as_str()),"configuration":{"agent":session.coworker,"model":session.model,"bundle_digest":session.bundle_digest,"tool_schema_digest":session.schema_digest},"audit_reference":{"session_id":session.id,"turn_id":q.turn_id,"execution_event_through":through,"capture_enabled":session.compliance.is_some()},"input":input,"events":result,"next_event_id":(after<through).then_some(after),"through_event_id":through,"coverage":{"source":"committed_execution_events","input_available":current.is_some()&&policy.user_input,"input_scope":"current_turn_only","omitted_for_size":omitted,"redaction":"best_effort_secret_patterns","captured_fields":policy,"not_an_audit_archive":true}}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn policy() -> crate::recording::EvidencePolicy {
        crate::recording::EvidencePolicy {
            user_input: true,
            assistant_output: true,
            tool_arguments: true,
            tool_results: true,
            max_bytes: 1024,
        }
    }
    #[test]
    fn evidence_keeps_accepted_text_but_not_reasoning_or_attempt_deltas() {
        let data = json!({"messages":[{"role":"assistant","content":[{"type":"output_text","text":"accepted"},{"type":"reasoning","text":"private"}]}],"provider_private":"hidden"});
        let result = project("assistant_message", &data, &policy())
            .unwrap()
            .to_string();
        assert!(result.contains("accepted"));
        assert!(!result.contains("private"));
        assert!(!result.contains("hidden"));
        assert!(project("assistant_delta", &data, &policy()).is_none());
        assert!(project("reasoning_delta", &data, &policy()).is_none());
    }
    #[test]
    fn evidence_excludes_both_blocked_result_shapes_and_preserves_uncertainty() {
        for data in [
            json!({"outputBlocked":true,"result":{"text":"candidate"}}),
            json!({"result":{"outputBlocked":true,"text":"candidate"}}),
        ] {
            assert!(project("tool_finished", &data, &policy()).is_none());
        }
        let value = project(
            "tool_finished",
            &json!({"call_id":"c","executed":true,"result":{"outcome":"unknown"}}),
            &policy(),
        )
        .unwrap();
        assert_eq!(value["result"]["outcome"], "unknown");
        assert_eq!(value["executed"], true);
        let recovery = project(
            "turn_recovered",
            &json!({"status":"interrupted","outcome_unknown_count":2,"automatic_replay":false}),
            &policy(),
        )
        .unwrap();
        assert_eq!(recovery["outcome_unknown_count"], 2);
        assert_eq!(recovery["automatic_replay"], false);

        let mut disabled = policy();
        disabled.tool_results = false;
        assert!(project("tool_finished", &value, &disabled).is_none());
    }
}
