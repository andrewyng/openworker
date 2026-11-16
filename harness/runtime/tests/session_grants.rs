#![cfg(any(windows, target_os = "linux"))]
use openworker_runtime::{execution::Backend, model::Model, runtime::Runtime};
use openworker_tools::{Tool, ToolContext, ToolDescriptor, ToolFuture, ToolOutput, ToolRegistry};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
};
struct Probe(&'static str, Arc<AtomicUsize>, String);
impl Tool for Probe {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: format!("test.{}", self.0),
            description: "Test counter; no OS effects".into(),
            version: self.2.clone(),
            input_schema: json!({"type":"object","properties":{"action":{"type":"string"}},"required":["action"],"additionalProperties":false}),
        }
    }
    fn call(&self, _: ToolContext, args: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            self.1.fetch_add(1, Ordering::SeqCst);
            Ok(ToolOutput::json(args))
        })
    }
}
struct Setup {
    tmp: tempfile::TempDir,
    calls: Arc<AtomicUsize>,
}
impl Setup {
    fn new() -> Self {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join("bundle/coworker")).unwrap();
        std::fs::create_dir(tmp.path().join("work")).unwrap();
        std::fs::write(
            tmp.path().join("bundle/coworker/manifest.md"),
            "---\nid: grants\nname: Grants\n---\nUse test tools.",
        )
        .unwrap();
        std::fs::write(
            tmp.path().join("bundle/tools.json"),
            r#"{"version":3,"tools":["test.probe","test.other"]}"#,
        )
        .unwrap();
        Self {
            tmp,
            calls: Default::default(),
        }
    }
    fn open(&self, version: &str) -> Arc<Runtime> {
        let mut r = ToolRegistry::default();
        for n in ["probe", "other"] {
            r.register(Probe(n, self.calls.clone(), version.into()))
                .unwrap();
        }
        Runtime::open_with_registry(
            &self.tmp.path().join("state"),
            &self.tmp.path().join("bundle"),
            Path::new(env!("CARGO_BIN_EXE_openworker")),
            Model::new("fixture".into(), true),
            Backend::Off,
            None,
            None,
            r,
        )
        .unwrap()
    }
    async fn session(&self, r: &Arc<Runtime>) -> String {
        r.dispatch(
            "session/create",
            json!({"name":"Grants","coworker":"grants","workspace":self.tmp.path().join("work")}),
        )
        .await
        .unwrap()["session"]["id"]
            .as_str()
            .unwrap()
            .into()
    }
}
async fn start(r: &Arc<Runtime>, sid: &str, steps: Value) {
    r.dispatch("turn/start",json!({"session_id":sid,"submission_id":uuid::Uuid::new_v4().to_string(),"text":format!("TEST_RESOURCE_GRANTS {steps}")})).await.unwrap();
}
async fn wait(r: &Arc<Runtime>, sid: &str, p: impl Fn(&Value) -> bool) -> Value {
    tokio::time::timeout(std::time::Duration::from_secs(60), async {
        loop {
            let v = r.snapshot(sid).unwrap();
            if p(&v) {
                return v;
            }
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap()
}
async fn pending(r: &Arc<Runtime>, sid: &str) -> Value {
    wait(r, sid, |v| {
        !v["interactions"].as_array().unwrap().is_empty()
    })
    .await["interactions"][0]
        .clone()
}
async fn done(r: &Arc<Runtime>, sid: &str) {
    let v = wait(r, sid, |v| {
        matches!(v["turn"]["status"].as_str(), Some("completed" | "failed"))
    })
    .await;
    assert_eq!(v["turn"]["status"], "completed");
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
}
async fn respond(r: &Arc<Runtime>, sid: &str, i: &Value, d: &str) -> Value {
    loop {
        match r
            .dispatch(
                "interaction/respond",
                json!({"session_id":sid,"interaction_id":i["id"],"decision":d}),
            )
            .await
        {
            Ok(v) => return v,
            Err(e) if e.to_string().contains("checkpointing") => {
                tokio::time::sleep(std::time::Duration::from_millis(20)).await
            }
            Err(e) => panic!("{e:#}"),
        }
    }
}
fn step(name: &str, action: &str) -> Value {
    json!({"name":name,"arguments":{"action":action}})
}
async fn grants(r: &Arc<Runtime>, sid: &str) -> Value {
    r.dispatch("permissions/list", json!({"session_id":sid}))
        .await
        .unwrap()["grants"]
        .clone()
}

#[tokio::test]
async fn session_grants_persist_cover_all_actions_and_remain_scoped_revocable_and_idempotent() {
    let f = Setup::new();
    let r = f.open("1");
    let sid = f.session(&r).await;
    start(
        &r,
        &sid,
        json!([
            step("test__probe", "observe"),
            step("test__probe", "type"),
            step("test__other", "observe")
        ]),
    )
    .await;
    let first = pending(&r, &sid).await;
    respond(&r, &sid, &first, "session").await;
    let other = pending(&r, &sid).await;
    assert_eq!(other["call"]["name"], "test__other");
    assert_eq!(f.calls.load(Ordering::SeqCst), 2);
    respond(&r, &sid, &other, "deny").await;
    done(&r, &sid).await;
    assert_eq!(
        respond(&r, &sid, &first, "session").await["duplicate"],
        true
    );
    let saved = grants(&r, &sid).await;
    assert_eq!(saved.as_array().unwrap().len(), 1);
    let gid = saved[0]["id"].clone();
    r.shutdown().await.unwrap();
    drop(r);
    let r = f.open("1");
    assert_eq!(grants(&r, &sid).await, saved);
    start(&r, &sid, json!([step("test__probe", "another-action")])).await;
    done(&r, &sid).await;
    assert_eq!(f.calls.load(Ordering::SeqCst), 3);
    let other_sid = f.session(&r).await;
    start(&r, &other_sid, json!([step("test__probe", "observe")])).await;
    let i = pending(&r, &other_sid).await;
    assert_eq!(
        r.dispatch(
            "permissions/revoke",
            json!({"session_id":other_sid,"grant_id":gid})
        )
        .await
        .unwrap()["revoked"],
        false
    );
    assert_eq!(grants(&r, &sid).await, saved);
    respond(&r, &other_sid, &i, "deny").await;
    done(&r, &other_sid).await;
    assert_eq!(
        r.dispatch(
            "permissions/revoke",
            json!({"session_id":sid,"grant_id":gid})
        )
        .await
        .unwrap()["revoked"],
        true
    );
    assert_eq!(
        r.dispatch(
            "permissions/revoke",
            json!({"session_id":sid,"grant_id":gid})
        )
        .await
        .unwrap()["already_absent"],
        true
    );
    start(&r, &sid, json!([step("test__probe", "click")])).await;
    let i = pending(&r, &sid).await;
    respond(&r, &sid, &i, "session").await;
    done(&r, &sid).await;
    let gid = grants(&r, &sid).await[0]["id"].clone();
    r.dispatch(
        "permissions/revoke",
        json!({"session_id":sid,"grant_id":gid}),
    )
    .await
    .unwrap();
    assert_eq!(respond(&r, &sid, &i, "session").await["duplicate"], true);
    assert!(grants(&r, &sid).await.as_array().unwrap().is_empty());
    r.shutdown().await.unwrap();
    drop(r);
    let r = f.open("1");
    assert!(grants(&r, &sid).await.as_array().unwrap().is_empty());
    r.shutdown().await.unwrap();
}

#[tokio::test]
async fn changed_native_implementation_cannot_reuse_grants() {
    let f = Setup::new();
    let r = f.open("1");
    let sid = f.session(&r).await;
    start(&r, &sid, json!([step("test__probe", "observe")])).await;
    let i = pending(&r, &sid).await;
    respond(&r, &sid, &i, "session").await;
    done(&r, &sid).await;
    r.shutdown().await.unwrap();
    drop(r);
    let r = f.open("2");
    assert_eq!(grants(&r, &sid).await[0]["active"], false);
    assert!(r
        .dispatch(
            "turn/start",
            json!({"session_id":sid,"submission_id":"changed","text":"test"})
        )
        .await
        .is_err());
    assert_eq!(f.calls.load(Ordering::SeqCst), 1);
    r.shutdown().await.unwrap();
}

#[tokio::test]
async fn failed_commit_cannot_partially_mint_a_session_grant() {
    let f = Setup::new();
    let r = f.open("1");
    let sid = f.session(&r).await;
    start(&r, &sid, json!([step("test__probe", "observe")])).await;
    let i = pending(&r, &sid).await;
    let db = rusqlite::Connection::open(f.tmp.path().join("state/runtime.sqlite3")).unwrap();
    db.execute_batch("CREATE TRIGGER fail_grant BEFORE INSERT ON events WHEN NEW.kind='interaction_resolved' BEGIN SELECT RAISE(ABORT,'injected commit failure'); END;").unwrap();
    assert!(r
        .dispatch(
            "interaction/respond",
            json!({"session_id":sid,"interaction_id":i["id"],"decision":"session"})
        )
        .await
        .is_err());
    assert!(grants(&r, &sid).await.as_array().unwrap().is_empty());
    assert_eq!(pending(&r, &sid).await["id"], i["id"]);
    assert_eq!(f.calls.load(Ordering::SeqCst), 0);
    db.execute_batch("DROP TRIGGER fail_grant").unwrap();
    drop(db);
    respond(&r, &sid, &i, "once").await;
    done(&r, &sid).await;
    assert!(grants(&r, &sid).await.as_array().unwrap().is_empty());
    r.shutdown().await.unwrap();
}

#[tokio::test]
async fn mcp_grants_cover_one_exact_route_but_never_remove_path_guards() {
    let f = Setup::new();
    std::fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant/tools.json"),
        f.tmp.path().join("bundle/tools.json"),
    )
    .unwrap();
    let r = f.open("1");
    let sid = f.session(&r).await;
    let write =
        |path: &str| json!({"name":"write_file","arguments":{"path":path,"content":"proof"}});
    start(&r,&sid,json!([write("one.txt"),write("two.txt"),{"name":"read_file","arguments":{"path":"two.txt"}}])).await;
    let i = pending(&r, &sid).await;
    respond(&r, &sid, &i, "session").await;
    let read = pending(&r, &sid).await;
    assert_eq!(read["call"]["name"], "read_file");
    respond(&r, &sid, &read, "once").await;
    done(&r, &sid).await;
    assert_eq!(
        std::fs::read_to_string(f.tmp.path().join("work/two.txt")).unwrap(),
        "proof"
    );
    start(&r, &sid, json!([write("../escape.txt")])).await;
    done(&r, &sid).await;
    assert!(!f.tmp.path().join("escape.txt").exists());
    assert!(r
        .store
        .lock()
        .unwrap()
        .events(&sid, 0)
        .unwrap()
        .iter()
        .any(|e| e.kind == "interaction_resolved" && e.data["origin"] == "hard_policy"));
    r.shutdown().await.unwrap();
}
