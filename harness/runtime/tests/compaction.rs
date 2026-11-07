use openworker_runtime::compaction as c;
use serde_json::{json, Value};

#[test]
fn shared_python_policy_fixtures() {
    for case in c::REFERENCE["cases"].as_array().unwrap() {
        let result: Value = match case["op"].as_str().unwrap() {
            "trigger" => json!(c::trigger(
                case["window"].as_u64().map(|v| v as usize),
                case["pct"].as_f64().unwrap(),
                case["cap"].as_u64().unwrap() as usize
            )),
            "estimate" => json!(c::estimate(case["messages"].as_array().unwrap())),
            "boundary" => json!(c::boundary(
                case["messages"].as_array().unwrap(),
                case["keep"].as_u64().unwrap() as usize
            )),
            "quality" => json!(c::quality(case["text"].as_str().unwrap())),
            "users" => {
                let users = case["messages"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap().to_string())
                    .collect::<Vec<_>>();
                let (kept, dropped) = c::fit_users(
                    &users,
                    case["prior_dropped"].as_u64().unwrap() as usize,
                    case["budget"].as_u64().unwrap() as usize,
                    case["pin"].as_u64().unwrap() as usize,
                );
                json!({"kept":kept,"dropped":dropped})
            }
            _ => panic!("unknown fixture"),
        };
        assert_eq!(result, case["expected"], "fixture {case}");
    }
}
#[test]
fn raw_reasoning_and_calls_remain_an_atomic_assistant_group() {
    let raw = vec![
        json!({"role":"user","content":"task"}),
        json!({"type":"reasoning","encrypted_content":"private"}),
        json!({"type":"function_call","call_id":"c","name":"read_file","arguments":"{}"}),
        json!({"type":"function_call_output","call_id":"c","output":"ok"}),
        json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}),
    ];
    let logical = c::logical(&raw);
    assert_eq!(
        logical.iter().map(|v| v.0).collect::<Vec<_>>(),
        vec![0, 1, 3, 4]
    );
    assert!(!serde_json::to_string(&logical).unwrap().contains("private"));
    let before = raw.clone();
    let _ = c::outbound(&raw, None);
    assert_eq!(raw, before);
}

#[test]
fn mechanical_state_does_not_claim_failed_or_denied_effects_succeeded() {
    let raw = vec![
        json!({"type":"function_call","call_id":"c1","name":"write_file","arguments":"{\"path\":\"never-created\"}"}),
        json!({"type":"function_call_output","call_id":"c1","output":"{\"error\":\"permission denied\"}"}),
        json!({"type":"function_call","call_id":"c2","name":"write_file","arguments":"{\"path\":\"denied\"}"}),
        json!({"type":"function_call_output","call_id":"c2","output":"{\"denied\":true}"}),
    ];
    let state = c::working_state(&raw);
    assert!(state.contains("tool reported failure"));
    assert!(state.contains("denied, not executed"));
    assert_eq!(
        state.contains("Files written"),
        c::DEVIATIONS["rust"]["failed_write_described_as_written"]
            .as_bool()
            .unwrap()
    );
}
