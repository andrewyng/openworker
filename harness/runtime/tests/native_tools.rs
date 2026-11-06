use openworker_runtime::{bundle::Bundle, permissions, resource_policy::Policy};
use openworker_tools::{
    Tool, ToolContext, ToolDescriptor, ToolError, ToolFuture, ToolOutput, ToolRegistry,
};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
};

struct Probe {
    name: String,
    version: String,
    calls: Arc<AtomicUsize>,
}
impl Tool for Probe {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: self.name.clone(),
            version: self.version.clone(),
            description: "SDK test tool".into(),
            input_schema: json!({"type":"object","properties":{"behavior":{"type":"string","enum":["ok","error","wait","panic"]}},"required":["behavior"],"additionalProperties":false}),
        }
    }
    fn call(&self, ctx: ToolContext, args: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            self.calls.fetch_add(1, Ordering::SeqCst);
            ctx.progress("started", json!({"test":true})).await?;
            match args["behavior"].as_str().unwrap() {
                "error" => Err(ToolError::new("synthetic_error", "Expected test failure")),
                "panic" => panic!("synthetic native panic"),
                "wait" => {
                    ctx.cancellation.cancelled().await;
                    Err(ToolError::cancelled())
                }
                _ => Ok(ToolOutput::json(
                    json!({"ok":true,"call_id":ctx.call_id,"session_id":ctx.session_id}),
                )),
            }
        })
    }
}
fn registry(version: &str, calls: Arc<AtomicUsize>) -> ToolRegistry {
    let mut r = ToolRegistry::default();
    for name in ["partner.probe", "partner.hidden"] {
        r.register(Probe {
            name: name.into(),
            version: version.into(),
            calls: calls.clone(),
        })
        .unwrap();
    }
    r
}
fn bundle(root: &Path, names: Value) {
    std::fs::create_dir_all(root.join("coworker")).unwrap();
    std::fs::write(
        root.join("coworker/manifest.md"),
        "---\nid: sdk-test\nname: SDK test\n---\nUse only selected tools.",
    )
    .unwrap();
    std::fs::write(
        root.join("tools.json"),
        json!({"version":3,"tools":names}).to_string(),
    )
    .unwrap();
}
#[test]
fn registry_requires_exact_names_and_rejects_ambiguity() {
    let tmp = tempfile::tempdir().unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let mut r = registry("1", calls.clone());
    assert!(r
        .register(Probe {
            name: "partner.probe".into(),
            version: "1".into(),
            calls: calls.clone()
        })
        .is_err());
    assert!(r
        .register_mcp(openworker_tools::McpServer {
            namespace: "partner".into(),
            program: "$OPENWORKER".into(),
            args: vec![]
        })
        .is_err());
    for names in [
        json!(["partner.missing"]),
        json!(["unknown.probe"]),
        json!(["partner.probe", "partner.probe"]),
        json!(["probe"]),
        json!([]),
    ] {
        bundle(tmp.path(), names);
        assert!(Bundle::load_with_registry(tmp.path(), &r).is_err());
    }
    r.register(Probe {
        name: "partner.a.b".into(),
        version: "1".into(),
        calls: calls.clone(),
    })
    .unwrap();
    r.register(Probe {
        name: "partner.a__b".into(),
        version: "1".into(),
        calls,
    })
    .unwrap();
    bundle(tmp.path(), json!(["partner.a.b", "partner.a__b"]));
    assert!(Bundle::load_with_registry(tmp.path(), &r).is_err());
}
#[test]
fn bundle_selects_only_named_tools_and_pins_registry_without_an_acl() {
    let tmp = tempfile::tempdir().unwrap();
    bundle(tmp.path(), json!(["partner.probe"]));
    let r = registry("1", Default::default());
    let b = Bundle::load_with_registry(tmp.path(), &r).unwrap();
    assert_eq!(b.tools.bindings.len(), 1);
    assert_eq!(b.tools.bindings["partner__probe"].tool, "partner.probe");
    assert_eq!(
        permissions::classify(&b, "partner__probe"),
        permissions::Category::External
    );
    let catalogs = std::collections::BTreeMap::from([(
        "partner".into(),
        json!({"tools":r.native_catalog("partner").iter().map(|d|json!({"name":d.qualified_name,"inputSchema":d.input_schema})).collect::<Vec<_>>()}),
    )]);
    let bound = openworker_runtime::bundle::bound_tools(&b, &catalogs).unwrap();
    assert_eq!(bound.len(), 1);
    assert_eq!(bound[0]["name"], "partner__probe");
    let changed =
        Bundle::load_with_registry(tmp.path(), &registry("2", Default::default())).unwrap();
    assert_eq!(changed.digest, b.digest);
    assert_ne!(changed.registry_digest, b.registry_digest);
    assert!(Bundle::load(tmp.path()).is_err());
    b.verify().unwrap();
}
#[test]
fn native_cannot_claim_sandbox_placement() {
    let tmp = tempfile::tempdir().unwrap();
    let base = tmp.path().canonicalize().unwrap();
    let root = base.join("bundle");
    bundle(&root, json!(["partner.probe"]));
    let b = Bundle::load_with_registry(&root, &registry("1", Default::default())).unwrap();
    let policy = base.join("policy.json");
    std::fs::write(&policy,json!({"version":1,"bundle_digest":b.digest,"profiles":{"test":{"sandbox":"openshell","workspace_roots":[base],"servers":{"partner":{"placement":"sandbox"}}}}}).to_string()).unwrap();
    let error = Policy::load(Some(&policy), &b).err().unwrap();
    assert!(error.to_string().contains("host placement"), "{error:#}");
}

#[cfg(target_os = "linux")]
mod execution {
    use super::*;
    use openworker_runtime::{execution::Backend, model::Model, runtime::Runtime};
    struct Setup {
        tmp: tempfile::TempDir,
        calls: Arc<AtomicUsize>,
    }
    impl Setup {
        fn new() -> Self {
            let tmp = tempfile::tempdir().unwrap();
            bundle(&tmp.path().join("bundle"), json!(["partner.probe"]));
            std::fs::create_dir(tmp.path().join("work")).unwrap();
            Self {
                tmp,
                calls: Default::default(),
            }
        }
        fn open(&self, version: &str) -> Arc<Runtime> {
            Runtime::open_with_registry(
                &self.tmp.path().join("state"),
                &self.tmp.path().join("bundle"),
                Path::new(env!("CARGO_BIN_EXE_openworker")),
                Model::new("fixture".into(), true),
                Backend::Off,
                None,
                None,
                registry(version, self.calls.clone()),
            )
            .unwrap()
        }
        async fn start(&self, rt: &Arc<Runtime>, mode: &str, args: Value) -> String {
            let s=rt.dispatch("session/create",json!({"name":"SDK acceptance","coworker":"sdk-test","workspace":self.tmp.path().join("work"),"permissions":{"mode":mode}})).await.unwrap();
            let id = s["session"]["id"].as_str().unwrap().to_owned();
            rt.dispatch("turn/start",json!({"session_id":id,"submission_id":"test","text":format!("TEST_RESOURCE_GRANTS {}",json!([{"name":"partner__probe","arguments":args}]))})).await.unwrap();
            id
        }
    }
    async fn wait(rt: &Arc<Runtime>, sid: &str, p: impl Fn(&Value) -> bool) -> Value {
        tokio::time::timeout(std::time::Duration::from_secs(10), async {
            loop {
                let s = rt.snapshot(sid).unwrap();
                if p(&s) {
                    return s;
                }
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap_or_else(|_| panic!("Timed out: {}", rt.snapshot(sid).unwrap()))
    }
    async fn finish(rt: &Arc<Runtime>, sid: &str) -> Value {
        wait(rt, sid, |s| {
            matches!(
                s["turn"]["status"].as_str(),
                Some("completed" | "failed" | "cancelled")
            )
        })
        .await
    }
    #[tokio::test]
    async fn approval_restart_execution_progress_and_result_share_the_journal() {
        let f = Setup::new();
        let rt = f.open("1");
        let sid = f.start(&rt, "ask", json!({"behavior":"ok"})).await;
        let s = wait(&rt, &sid, |s| {
            !s["interactions"].as_array().unwrap().is_empty()
        })
        .await;
        let iid = s["interactions"][0]["id"].clone();
        assert_eq!(f.calls.load(Ordering::SeqCst), 0);
        rt.shutdown().await.unwrap();
        drop(rt);
        let rt = f.open("1");
        let s = rt
            .dispatch("session/attach", json!({"session_id":sid}))
            .await
            .unwrap();
        assert_eq!(s["interactions"][0]["id"], iid);
        rt.dispatch(
            "interaction/respond",
            json!({"session_id":sid,"interaction_id":iid,"decision":"once"}),
        )
        .await
        .unwrap();
        assert_eq!(finish(&rt, &sid).await["turn"]["status"], "completed");
        assert_eq!(f.calls.load(Ordering::SeqCst), 1);
        let events = rt.store.lock().unwrap().events(&sid, 0).unwrap();
        let progress = events.iter().find(|e| e.kind == "tool_progress").unwrap();
        assert_eq!(progress.data["qualified_name"], "partner.probe");
        let started = events.iter().find(|e| e.kind == "tool_started").unwrap();
        assert_eq!(started.data["enforcement"], "none");
        let done = events.iter().find(|e| e.kind == "tool_finished").unwrap();
        assert_eq!(done.data["result"]["structuredContent"]["session_id"], sid);
        assert_eq!(
            progress.data["call_id"],
            done.data["result"]["structuredContent"]["call_id"]
        );
        rt.shutdown().await.unwrap();
        drop(rt);
        let rt = f.open("1");
        assert_eq!(
            rt.dispatch("session/attach", json!({"session_id":sid}))
                .await
                .unwrap()["turn"]["status"],
            "completed"
        );
        assert_eq!(f.calls.load(Ordering::SeqCst), 1);
        rt.shutdown().await.unwrap();
    }
    #[tokio::test]
    async fn denied_or_invalid_arguments_never_reach_native_code() {
        let f = Setup::new();
        let rt = f.open("1");
        let sid = f.start(&rt, "ask", json!({"behavior":"ok"})).await;
        let s = wait(&rt, &sid, |s| {
            !s["interactions"].as_array().unwrap().is_empty()
        })
        .await;
        rt.dispatch(
            "interaction/respond",
            json!({"session_id":sid,"interaction_id":s["interactions"][0]["id"],"decision":"deny"}),
        )
        .await
        .unwrap();
        assert_eq!(finish(&rt, &sid).await["turn"]["status"], "completed");
        let invalid = f
            .start(&rt, "bypass", json!({"behavior":"not-in-schema"}))
            .await;
        assert_eq!(finish(&rt, &invalid).await["turn"]["status"], "failed");
        assert_eq!(f.calls.load(Ordering::SeqCst), 0);
        rt.shutdown().await.unwrap();
    }
    #[tokio::test]
    async fn errors_are_structured_but_panics_stop_without_automatic_replay() {
        let f = Setup::new();
        let rt = f.open("1");
        let sid = f.start(&rt, "bypass", json!({"behavior":"error"})).await;
        assert_eq!(finish(&rt, &sid).await["turn"]["status"], "completed");
        let events = rt.store.lock().unwrap().events(&sid, 0).unwrap();
        let done = events.iter().find(|e| e.kind == "tool_finished").unwrap();
        assert_eq!(done.data["result"]["error"]["code"], "synthetic_error");
        assert_eq!(done.data["result"]["isError"], true);
        let sid = f.start(&rt, "bypass", json!({"behavior":"panic"})).await;
        assert_eq!(finish(&rt, &sid).await["turn"]["status"], "failed");
        assert_eq!(f.calls.load(Ordering::SeqCst), 2);
        rt.shutdown().await.unwrap();
    }
    #[tokio::test]
    async fn cooperative_native_cancellation_cannot_publish_a_late_result() {
        let f = Setup::new();
        let rt = f.open("1");
        let sid = f.start(&rt, "bypass", json!({"behavior":"wait"})).await;
        wait(&rt, &sid, |_| f.calls.load(Ordering::SeqCst) > 0).await;
        let s = rt.snapshot(&sid).unwrap();
        rt.dispatch(
            "turn/cancel",
            json!({"session_id":sid,"turn_id":s["turn"]["id"]}),
        )
        .await
        .unwrap();
        assert_eq!(finish(&rt, &sid).await["turn"]["status"], "cancelled");
        assert!(!rt
            .store
            .lock()
            .unwrap()
            .events(&sid, 0)
            .unwrap()
            .iter()
            .any(|e| e.kind == "tool_finished"));
        rt.shutdown().await.unwrap();
    }
    #[tokio::test]
    async fn changed_implementation_version_cannot_resume_old_approval() {
        let f = Setup::new();
        let rt = f.open("1");
        let sid = f.start(&rt, "ask", json!({"behavior":"ok"})).await;
        let s = wait(&rt, &sid, |s| {
            !s["interactions"].as_array().unwrap().is_empty()
        })
        .await;
        rt.shutdown().await.unwrap();
        drop(rt);
        let rt = f.open("2");
        assert!(rt.dispatch("interaction/respond",json!({"session_id":sid,"interaction_id":s["interactions"][0]["id"],"decision":"once"})).await.is_err());
        assert_eq!(f.calls.load(Ordering::SeqCst), 0);
        rt.shutdown().await.unwrap();
    }
}
