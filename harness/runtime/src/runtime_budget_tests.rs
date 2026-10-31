use super::*;
use crate::{
    budget::Limits,
    provider::{Completion, Usage},
};
use std::sync::atomic::AtomicUsize;

fn message(text: &str) -> Value {
    json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":text}]})
}
fn plan(n: usize) -> Value {
    json!({"type":"function_call","call_id":format!("plan-{n}"),"name":"update_plan","arguments":json!({"expected_revision":n,"explanation":"Progress checkpoint","steps":[{"id":"build","description":"Build and verify","status":"in_progress"}]}).to_string()})
}
async fn fixture(
    model: Model,
    limits: Option<Limits>,
) -> (tempfile::TempDir, Arc<Runtime>, String) {
    let tmp = tempfile::tempdir().unwrap();
    let bundle = tmp.path().join("bundle");
    std::fs::create_dir_all(bundle.join("coworker")).unwrap();
    std::fs::create_dir(tmp.path().join("work")).unwrap();
    std::fs::write(tmp.path().join("runner"), b"\x7fELF-not-executed").unwrap();
    std::fs::write(
        bundle.join("coworker/manifest.md"),
        "---\nid: budget-test\nplanning: true\n---\nBuild safely.",
    )
    .unwrap();
    std::fs::write(
        bundle.join("tools.json"),
        include_str!("../../examples/assistant/tools.json"),
    )
    .unwrap();
    let rt = Runtime::open(
        &tmp.path().join("state"),
        &bundle,
        &tmp.path().join("runner"),
        model,
    )
    .unwrap();
    let sid=rt.dispatch("session/create",json!({"coworker":"budget-test","workspace":tmp.path().join("work"),"name":"Limits","compaction":{"enabled":false}})).await.unwrap()["session"]["id"].as_str().unwrap().to_owned();
    rt.store
        .lock()
        .unwrap()
        .accept_with_limits(&sid, "test", "Build", limits)
        .unwrap();
    (tmp, rt, sid)
}
fn model(f: impl Fn(usize, String) -> Value + Send + Sync + 'static, output: u64) -> Model {
    let count = Arc::new(AtomicUsize::new(0));
    let mut m = Model::new("test".into(), true);
    m.sample_hook = Some(Arc::new(move |i, _, tx| {
        let summary = i.contains("configured turn budget is exhausted");
        let out = f(count.fetch_add(1, Ordering::SeqCst), i);
        Box::pin(async move {
            tx.send(Progress::Complete(Completion {
                output: vec![out],
                usage: Some(Usage {
                    output_tokens: if summary { output.min(32) } else { output },
                    ..Default::default()
                }),
            }))
            .await
            .unwrap();
            Ok(())
        })
    }));
    m
}
#[tokio::test]
async fn a_turn_can_finish_after_more_than_twenty_model_calls() {
    let (_tmp, rt, sid) = fixture(
        model(
            |n, _| {
                if n < 25 {
                    plan(n)
                } else {
                    message("Finished 25 steps")
                }
            },
            1,
        ),
        None,
    )
    .await;
    rt.drive(&sid, Default::default(), vec![crate::planning::schema()])
        .await
        .unwrap();
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    let t = s.turn.unwrap();
    assert_eq!(t.status, "completed");
    assert_eq!(t.budget.model_calls, 26);
    assert_eq!(s.plan.unwrap().revision, 25);
}
#[tokio::test]
async fn step_exhaustion_rejects_queued_effect_and_gets_one_summary() {
    let (_tmp, rt, sid) = fixture(
        model(
            |n, i| {
                if n == 0 {
                    plan(0)
                } else {
                    assert!(i.contains("configured turn budget is exhausted"));
                    message("Stopped: planning is incomplete.")
                }
            },
            1,
        ),
        Some(Limits {
            max_model_calls: 1,
            ..Default::default()
        }),
    )
    .await;
    rt.drive(&sid, Default::default(), vec![crate::planning::schema()])
        .await
        .unwrap();
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    assert!(s.plan.is_none());
    let t = s.turn.unwrap();
    assert_eq!(t.budget.model_calls, 1);
    assert!(t.budget.summary_started);
    assert_eq!(t.budget.stop_reason.as_deref(), Some("model_call_limit"));
    assert_eq!(t.status, "completed");
    let e = rt.store.lock().unwrap().events(&sid, 0).unwrap();
    assert!(e
        .iter()
        .any(|e| e.data["result"]["code"] == "turn_limit_exceeded"
            && e.data["result"]["executed"] == false));
    assert_eq!(
        e.iter()
            .filter(|e| e.kind == "turn_budget_exceeded")
            .count(),
        1
    );
}
#[tokio::test]
async fn output_exhaustion_reserves_a_summary_within_the_total() {
    let (_tmp, rt, sid) = fixture(
        model(
            |n, _| {
                if n == 0 {
                    plan(0)
                } else {
                    message("Configured output limit reached; plan incomplete.")
                }
            },
            640,
        ),
        Some(Limits {
            max_model_calls: 10,
            max_output_tokens: 768,
            summary_tokens: 128,
        }),
    )
    .await;
    // The fake provider's first request consumes the entire work allowance.
    rt.drive(&sid, Default::default(), vec![crate::planning::schema()])
        .await
        .unwrap();
    let t = rt.store.lock().unwrap().get(&sid).unwrap().turn.unwrap();
    assert_eq!(t.budget.stop_reason.as_deref(), Some("output_token_limit"));
    assert_eq!(t.budget.model_calls, 1);
    assert!(t.budget.charged_output_tokens <= 768);
}
#[tokio::test]
async fn invalid_arguments_are_corrected_before_any_execution() {
    let (_tmp,rt,sid)=fixture(model(|n,_|match n{0=>json!({"type":"function_call","call_id":"invalid","name":"update_plan","arguments":"{}"}),1=>plan(0),_=>message("Corrected and done")},1),None).await;
    rt.drive(&sid, Default::default(), vec![crate::planning::schema()])
        .await
        .unwrap();
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    assert_eq!(s.plan.unwrap().revision, 1);
    assert_eq!(s.turn.unwrap().budget.validation_errors, 1);
    let events = rt.store.lock().unwrap().events(&sid, 0).unwrap();
    assert!(events
        .iter()
        .any(|e| e.kind == "tool_validation_failed" && e.data["executed"] == false));
}
#[tokio::test]
async fn repeated_invalid_arguments_stop_with_summary() {
    let (_tmp,rt,sid)=fixture(model(|n,i|if n<5{json!({"type":"function_call","call_id":format!("bad-{n}"),"name":"update_plan","arguments":"{}"})}else{assert!(i.contains("Tool argument correction limit"));message("Stopped: repeated invalid tool arguments.")},1),None).await;
    rt.drive(&sid, Default::default(), vec![crate::planning::schema()])
        .await
        .unwrap();
    let t = rt.store.lock().unwrap().get(&sid).unwrap().turn.unwrap();
    assert_eq!(t.budget.validation_errors, 5);
    assert!(t.budget.summary_started);
    assert_eq!(
        t.budget.stop_reason.as_deref(),
        Some("argument_correction_limit")
    );
}
#[tokio::test]
async fn submission_id_binds_the_limit_override() {
    let (_tmp, rt, sid) = fixture(
        model(|_, _| message("Done"), 1),
        Some(Limits {
            max_model_calls: 2,
            ..Default::default()
        }),
    )
    .await;
    let mut store = rt.store.lock().unwrap();
    assert!(
        !store
            .accept_with_limits(
                &sid,
                "test",
                "Build",
                Some(Limits {
                    max_model_calls: 2,
                    ..Default::default()
                })
            )
            .unwrap()
            .1
    );
    assert!(store
        .accept_with_limits(
            &sid,
            "test",
            "Build",
            Some(Limits {
                max_model_calls: 3,
                ..Default::default()
            })
        )
        .is_err());
    let state = store.get(&sid).unwrap();
    assert_eq!(state.turn.unwrap().budget.limits.max_model_calls, 2);
}

#[tokio::test]
async fn rejected_calls_and_budget_stops_export_content_free_events() {
    struct Capture(Arc<Mutex<Vec<crate::telemetry::Record>>>);
    impl crate::telemetry::Sink for Capture {
        fn export(
            &mut self,
            records: &[crate::telemetry::Record],
            _: crate::telemetry::Snapshot,
        ) -> bool {
            self.0.lock().unwrap().extend_from_slice(records);
            true
        }
    }
    let rows = Arc::new(Mutex::new(Vec::new()));
    let (telemetry, controller) =
        crate::telemetry::Telemetry::start(Capture(rows.clone()), 128).unwrap();
    let (_tmp,mut rt,sid)=fixture(model(|n,_|if n==0{json!({"type":"function_call","call_id":"invalid","name":"update_plan","arguments":"{\"SENSITIVE_ARGUMENT\":true}"})}else{message("Stopped because the limit was reached.")},1),Some(Limits{max_model_calls:2,..Default::default()})).await;
    Arc::get_mut(&mut rt).unwrap().telemetry = telemetry;
    rt.drive(&sid, Default::default(), vec![crate::planning::schema()])
        .await
        .unwrap();
    // First invalid call is rejected, second completes; explicitly record the
    // separate budget-stop path in a new turn with a queued tool at its limit.
    rt.store
        .lock()
        .unwrap()
        .accept_with_limits(
            &sid,
            "limited",
            "Next",
            Some(Limits {
                max_model_calls: 1,
                ..Default::default()
            }),
        )
        .unwrap();
    rt.update(&sid, "test_budget", json!({}), |s| {
        s.turn.as_mut().unwrap().budget.model_calls = 1;
        s.turn.as_mut().unwrap().queue = vec![plan(0)];
        Ok(())
    })
    .unwrap();
    rt.drive(&sid, Default::default(), vec![crate::planning::schema()])
        .await
        .unwrap();
    assert!(controller.shutdown(std::time::Duration::from_secs(2)));
    let rows = rows.lock().unwrap();
    assert!(rows.iter().any(|r| r.operation == Op::ToolValidationFailed));
    assert!(rows.iter().any(|r| r.operation == Op::BudgetExceeded));
    assert!(!format!("{rows:?}").contains("SENSITIVE_ARGUMENT"));
}

#[tokio::test]
async fn incomplete_usage_cannot_refund_a_reserved_output_allowance() {
    let count = Arc::new(AtomicUsize::new(0));
    let mut m = Model::new("test".into(), true);
    m.sample_hook = Some(Arc::new(move |_, _, tx| {
        let first = count.fetch_add(1, Ordering::SeqCst) == 0;
        Box::pin(async move {
            if first {
                tx.send(Progress::Usage(crate::telemetry::TokenUsage {
                    output: Some(1),
                    ..Default::default()
                }))
                .await
                .unwrap();
                return Err(
                    crate::provider::Error::new(crate::provider::Failure::InvalidResponse).into(),
                );
            }
            tx.send(Progress::Complete(Completion {
                output: vec![message(
                    "Stopped after an incomplete response used the reserved allowance.",
                )],
                usage: Some(Usage {
                    output_tokens: 1,
                    ..Default::default()
                }),
            }))
            .await
            .unwrap();
            Ok(())
        })
    }));
    let (_tmp, rt, sid) = fixture(
        m,
        Some(Limits {
            max_model_calls: 20,
            max_output_tokens: 1024,
            summary_tokens: 256,
        }),
    )
    .await;
    rt.drive(&sid, Default::default(), vec![crate::planning::schema()])
        .await
        .unwrap();
    let turn = rt.store.lock().unwrap().get(&sid).unwrap().turn.unwrap();
    assert_eq!(turn.status, "completed");
    assert_eq!(
        turn.budget.stop_reason.as_deref(),
        Some("output_token_limit")
    );
    assert_eq!(turn.budget.charged_output_tokens, 769);
    assert_eq!(turn.budget.model_calls, 1);
}

#[tokio::test]
async fn exhausted_compaction_finishes_instead_of_parking_another_question() {
    let mut m = model(
        |_, _| message("Stopped at the configured limit; compaction is incomplete."),
        1,
    );
    m.summary_hook = Some(Arc::new(|_| {
        Box::pin(async { anyhow::bail!("Unavailable summarizer") })
    }));
    let (_tmp, rt, sid) = fixture(
        m,
        Some(Limits {
            max_model_calls: 2,
            ..Default::default()
        }),
    )
    .await;
    {
        let mut store = rt.store.lock().unwrap();
        let mut s = store.get(&sid).unwrap();
        s.compaction_config.enabled = true;
        s.compaction_config.context_window = 1024;
        s.compaction_config.cap_tokens = 512;
        s.compaction_config.summary_max_tokens = 512;
        s.history.extend([
            json!({"role":"user","content":"earlier task ".repeat(1000)}),
            message("Earlier work"),
            json!({"role":"user","content":"Current task"}),
        ]);
        store.save(&s, "compaction_fixture", json!({})).unwrap();
    }
    assert!(matches!(
        Box::pin(rt.compact(&sid, true)).await.unwrap(),
        CompactResult::Skipped
    ));
    Box::pin(rt.drive(&sid, Default::default(), vec![]))
        .await
        .unwrap();
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    let t = s.turn.unwrap();
    assert_eq!(t.status, "completed");
    assert_eq!(t.budget.model_calls, 2);
    assert_eq!(t.budget.stop_reason.as_deref(), Some("model_call_limit"));
    assert!(t.budget.summary_started);
    assert!(!s.interactions.iter().any(|i| i.response.is_none()));
}
