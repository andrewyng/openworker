#![cfg(any(windows, target_os = "linux"))]
use openworker_runtime::{execution::Backend, model::Model, runtime::Runtime, telemetry::*};
use openworker_tools::{Tool, ToolContext, ToolDescriptor, ToolFuture, ToolOutput, ToolRegistry};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{Arc, Mutex},
    time::Duration,
};
struct Capture(Arc<Mutex<Vec<Record>>>);
impl Sink for Capture {
    fn export(&mut self, r: &[Record], _: Snapshot) -> bool {
        self.0.lock().unwrap().extend_from_slice(r);
        true
    }
}
struct SecretTool;
impl Tool for SecretTool {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: "private.probe".into(),
            description: "SENSITIVE_DESCRIPTION".into(),
            version: "1".into(),
            input_schema: json!({"type":"object","properties":{"secret":{"type":"string"}},"required":["secret"],"additionalProperties":false}),
        }
    }
    fn call(&self, _: ToolContext, _: Value) -> ToolFuture<'_> {
        Box::pin(async { Ok(ToolOutput::json(json!({"result":"SENSITIVE_TOOL_OUTPUT"}))) })
    }
}
async fn wait(rt: &Arc<Runtime>, sid: &str, state: &str) -> Value {
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            let v = rt.snapshot(sid).unwrap();
            if v["turn"]["status"] == state && !rt.is_busy(sid) {
                return v;
            }
            assert!(
                !(v["turn"]["status"] == "failed" && !rt.is_busy(sid)),
                "{v}"
            );
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap()
}
#[tokio::test(flavor = "multi_thread")]
async fn actual_agent_approval_resume_has_hierarchy_without_exporting_content() {
    let root = tempfile::tempdir().unwrap();
    let bundle = root.path().join("bundle");
    std::fs::create_dir_all(bundle.join("coworker")).unwrap();
    std::fs::create_dir(root.path().join("work")).unwrap();
    std::fs::write(
        bundle.join("coworker/manifest.md"),
        "---\nid: trace-test\nname: Trace Test\nplanning: true\nmemory: true\nscheduling: true\n---\nSENSITIVE_INSTRUCTIONS",
    )
    .unwrap();
    std::fs::write(
        bundle.join("tools.json"),
        r#"{"version":3,"tools":["private.probe"]}"#,
    )
    .unwrap();
    let mut registry = ToolRegistry::default();
    registry.register(SecretTool).unwrap();
    let rows = Arc::new(Mutex::new(vec![]));
    let (telemetry, controller) = Telemetry::start_with_content(
        Capture(rows.clone()),
        2048,
        ContentPolicy {
            capture: openworker_runtime::telemetry::Capture::Metadata,
            ..Default::default()
        },
    )
    .unwrap();
    let mut rt = Runtime::open_instrumented(
        &root.path().join("state"),
        &bundle,
        Path::new(env!("CARGO_BIN_EXE_openworker")),
        Model::new("fixture".into(), true),
        Backend::Off,
        None,
        None,
        registry,
        None,
        telemetry.clone(),
    )
    .unwrap();
    let s=rt.dispatch("session/create",json!({"name":"SENSITIVE_NAME","coworker":"trace-test","workspace":root.path().join("work")})).await.unwrap();
    let sid = s["session"]["id"].as_str().unwrap();
    for (n, decision) in [(0, "once"), (1, "deny"), (2, "cancel"), (3, "answer")] {
        let steps = if n < 2 {
            json!([{"name":"private__probe","arguments":{"secret":"SENSITIVE_ARGUMENT"}}])
        } else {
            json!([{"name":"ask_user","arguments":{"question":"SENSITIVE_QUESTION","options":["Yes"]}}])
        };
        rt.dispatch("turn/start",json!({"session_id":sid,"submission_id":format!("request-{n}"),"text":format!("TEST_RESOURCE_GRANTS {steps}")})).await.unwrap();
        let waiting = wait(&rt, sid, "waiting").await;
        let id = waiting["interactions"][0]["id"].clone();
        if n == 0 {
            assert_eq!(waiting["turn"]["observation"]["segments"], 1);
            assert!(waiting["interactions"][0]["requested_at_ms"]
                .as_u64()
                .is_some());
            rt.shutdown().await.unwrap();
            drop(rt);
            let mut registry = ToolRegistry::default();
            registry.register(SecretTool).unwrap();
            rt = Runtime::open_instrumented(
                &root.path().join("state"),
                &bundle,
                Path::new(env!("CARGO_BIN_EXE_openworker")),
                Model::new("fixture".into(), true),
                Backend::Off,
                None,
                None,
                registry,
                None,
                telemetry.clone(),
            )
            .unwrap();
            let attached = rt
                .dispatch("session/attach", json!({"session_id":sid}))
                .await
                .unwrap();
            assert_eq!(
                attached["turn"]["observation"],
                waiting["turn"]["observation"]
            );
        }
        if decision == "cancel" {
            rt.dispatch(
                "turn/cancel",
                json!({"session_id":sid,"turn_id":waiting["turn"]["id"]}),
            )
            .await
            .unwrap();
            let cancelled = wait(&rt, sid, "cancelled").await;
            assert!(cancelled["interactions"].as_array().unwrap().is_empty());
            continue;
        }
        rt.dispatch(
            "interaction/respond",
            if decision == "answer" {
                json!({"session_id":sid,"interaction_id":id,"answer":"Yes"})
            } else {
                json!({"session_id":sid,"interaction_id":id,"decision":decision})
            },
        )
        .await
        .unwrap();
        let finished = wait(&rt, sid, "completed").await;
        assert_eq!(finished["turn"]["observation"]["segments"], 2);
    }
    let internal=rt.dispatch("session/create",json!({"name":"Internal tools","coworker":"trace-test","workspace":root.path().join("work"),"permissions":{"mode":"bypass"}})).await.unwrap();
    let internal_sid = internal["session"]["id"].as_str().unwrap();
    let steps = json!([
        {"name":"update_plan","arguments":{"expected_revision":0,"explanation":"SENSITIVE_EXPLANATION","steps":[{"id":"done","description":"SENSITIVE_PLAN","status":"completed"}]}},
        {"name":"memory_remember","arguments":{"summary":"SENSITIVE_MEMORY","content":"SENSITIVE_CONTENT"}},
        {"name":"current_time","arguments":{}}
    ]);
    rt.dispatch("turn/start",json!({"session_id":internal_sid,"submission_id":"internal","text":format!("TEST_RESOURCE_GRANTS {steps}")})).await.unwrap();
    wait(&rt, internal_sid, "completed").await;
    rt.shutdown().await.unwrap();
    assert!(controller.shutdown(Duration::from_secs(3)));
    let rows = rows.lock().unwrap();
    assert!(!format!("{rows:?}").contains("SENSITIVE"));
    assert!(!format!("{rows:?}").contains(sid));
    let tool = rows
        .iter()
        .filter(|r| {
            r.operation == Operation::Tool
                && r.attributes
                    .iter()
                    .any(|(k, v)| *k == "gen_ai.tool.name" && v == "private__probe")
        })
        .collect::<Vec<_>>();
    assert_eq!(tool.len(), 1);
    assert_eq!(tool[0].outcome, Outcome::Ok);
    let parent = tool[0].parent.unwrap();
    assert!(rows
        .iter()
        .any(|r| r.context == parent && r.operation == Operation::Turn));
    assert_eq!(
        rows.iter()
            .filter(|r| r.operation == Operation::Turn && r.outcome == Outcome::Waiting)
            .count(),
        4
    );
    assert!(rows
        .iter()
        .any(|r| r.operation == Operation::ApprovalResolved && r.outcome == Outcome::Denied));
    let segments: Vec<_> = rows
        .iter()
        .filter(|r| {
            r.operation == Operation::Turn
                && r.identity.session == Identity::new(Some(sid), None).session
        })
        .collect();
    assert_eq!(segments.len(), 7);
    for waiting in segments.iter().filter(|r| r.outcome == Outcome::Waiting) {
        let Some(resumed) = segments.iter().find(|r| r.links.contains(&waiting.context)) else {
            assert!(rows.iter().any(|r| r.operation == Operation::QuestionWait
                && r.outcome == Outcome::Cancelled
                && r.context.trace == waiting.context.trace));
            continue;
        };
        assert_eq!(waiting.context.trace, resumed.context.trace);
        assert_eq!(waiting.turn_hash, resumed.turn_hash);
        assert_ne!(waiting.context.span, resumed.context.span);
    }
    assert_eq!(
        rows.iter()
            .filter(|r| r.operation == Operation::ApprovalWait)
            .count(),
        2
    );
    assert!(rows
        .iter()
        .filter(|r| r.operation == Operation::ApprovalWait)
        .all(|r| r.timing_known && r.turn_hash.is_some()));
    assert_eq!(
        rows.iter()
            .filter(|r| r.operation == Operation::QuestionWait)
            .count(),
        2
    );
    assert_eq!(
        rows.iter()
            .filter(|r| r.operation == Operation::QuestionResolved)
            .count(),
        1
    );
    assert!(tool[0].output_bytes.is_some_and(|n| n > 0));
    assert!(rows.iter().any(|r| r.operation == Operation::Model));
    assert!(rows.iter().all(|r| r.input_tokens.is_none()));
    for name in ["update_plan", "memory_remember", "current_time"] {
        let record = rows
            .iter()
            .find(|r| {
                r.operation == Operation::Tool
                    && r.attributes
                        .iter()
                        .any(|(k, v)| *k == "gen_ai.tool.name" && v == name)
            })
            .unwrap();
        assert_eq!(record.outcome, Outcome::Ok);
        assert!(record.output_bytes.is_some_and(|n| n > 0));
        assert!(record.turn_hash.is_some());
        assert!(record
            .attributes
            .iter()
            .any(|(k, v)| *k == "openworker.sandbox.enforcement" && v == "session-metadata-only"));
    }
}
