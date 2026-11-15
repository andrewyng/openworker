use super::*;
use crate::planning::{self, Plan};

fn args(revision: u64) -> Value {
    json!({"expected_revision":revision,"explanation":"Investigate first",
        "steps":[{"id":"inspect","description":"Read the repository","status":"in_progress"},
                 {"id":"verify","description":"Verify the result","status":"pending"}]})
}

async fn fixture(enabled: bool, mode: Mode) -> (tempfile::TempDir, Arc<Runtime>, String) {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::write(tmp.path().join("runner"), b"\x7fELF-test-only").unwrap();
    std::fs::create_dir(tmp.path().join("work")).unwrap();
    let bundle = tmp.path().join("bundle");
    std::fs::create_dir_all(bundle.join("coworker")).unwrap();
    std::fs::write(
        bundle.join("coworker/manifest.md"),
        format!("---\nid: planner\nplanning: {enabled}\n---\nUse a plan when useful."),
    )
    .unwrap();
    std::fs::write(
        bundle.join("tools.json"),
        include_str!("../../examples/assistant/tools.json"),
    )
    .unwrap();
    let mut model = Model::new("fixture".into(), true);
    model.sample_hook = Some(Arc::new(|_, _, tx| {
        Box::pin(async move {
            tx.send(Progress::Complete(crate::provider::Completion {
            output: vec![json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"Done"}]})],
            usage: None,
        })).await.unwrap();
            Ok(())
        })
    }));
    let rt = Runtime::open(
        &tmp.path().join("state"),
        &bundle,
        &tmp.path().join("runner"),
        model,
    )
    .unwrap();
    let sid = rt.dispatch("session/create", json!({"coworker":"planner","name":"Plan","workspace":tmp.path().join("work"),"permissions":{"mode":mode},"compaction":{"cap_tokens":512,"auto_trim_on_failure":true}})).await.unwrap()["session"]["id"].as_str().unwrap().to_owned();
    rt.store
        .lock()
        .unwrap()
        .accept(&sid, "submit", "Inspect only; do not deploy.")
        .unwrap();
    (tmp, rt, sid)
}

fn queue(rt: &Runtime, sid: &str, args: &Value) -> (String, Value) {
    let call = json!({"type":"function_call","call_id":crate::id("plan"),"name":planning::TOOL,"arguments":args.to_string()});
    let s = rt
        .update(sid, "fixture_queue", json!({}), |s| {
            s.schema_digest = Some("fixture-schema".into());
            s.history.push(call.clone());
            let t = s.turn.as_mut().unwrap();
            t.queue = vec![call.clone()];
            t.status = "running".into();
            Ok(())
        })
        .unwrap();
    (s.turn.unwrap().id, call)
}

#[test]
fn validation_covers_bounds_status_ids_and_stale_revision() {
    let good = args(0);
    validate_arguments(&planning::schema()["parameters"], &good).unwrap();
    let plan = planning::next(None, "turn", "call", &good).unwrap();
    assert_eq!(plan.revision, 1);
    assert!(planning::next(Some(&plan), "turn", "call", &good).is_err());
    let mut cases = Vec::new();
    let mut v = good.clone();
    v["steps"][1]["id"] = json!("inspect");
    cases.push(v);
    let mut v = good.clone();
    v["steps"][1]["status"] = json!("in_progress");
    cases.push(v);
    let mut v = good.clone();
    v["steps"][0]["description"] = json!("   ");
    cases.push(v);
    let mut v = good.clone();
    v["steps"][0]["description"] = json!("🦀".repeat(61));
    cases.push(v);
    let mut v = good.clone();
    v["steps"][0]["description"] = json!("hidden\ncontrol");
    cases.push(v);
    let mut v = good.clone();
    v["explanation"] = json!("x".repeat(501));
    cases.push(v);
    let mut v = good.clone();
    v["steps"] = json!(vec![good["steps"][0].clone(); 33]);
    cases.push(v);
    let mut v = good.clone();
    v["steps"][0]["id"] = json!("../other");
    cases.push(v);
    let mut v = good.clone();
    v["steps"][0]["status"] = json!("approved");
    cases.push(v);
    let mut v = good.clone();
    v["permission"] = json!("bypass");
    cases.push(v);
    for bad in cases {
        assert!(
            planning::next(None, "turn", "call", &bad).is_err(),
            "accepted {bad}"
        );
    }
    let clear = json!({"expected_revision":1,"explanation":"Replanning","steps":[]});
    assert!(planning::next(Some(&plan), "next-turn", "clear", &clear)
        .unwrap()
        .steps
        .is_empty());
}

#[tokio::test]
async fn drive_updates_plan_without_approval_but_actual_actions_still_ask() {
    for mode in [Mode::Ask, Mode::AutoApprove, Mode::Bypass] {
        let (_tmp, rt, sid) = fixture(true, mode).await;
        queue(&rt, &sid, &args(0));
        let mut events = rt.events.subscribe();
        rt.drive(&sid, BTreeMap::new(), vec![planning::schema()])
            .await
            .unwrap();
        let snapshot = rt.snapshot(&sid).unwrap();
        assert_eq!(snapshot["plan"]["revision"], 1);
        assert_eq!(snapshot["turn"]["status"], "completed");
        // Completing a turn is not proof that its checklist steps finished.
        assert_eq!(snapshot["plan"]["steps"][0]["status"], "in_progress");
        let s = rt.store.lock().unwrap().get(&sid).unwrap();
        assert!(s.interactions.is_empty());
        assert!(s.approval_grants.is_empty());
        assert!(s.turn.as_ref().unwrap().queue.is_empty());
        let event = events.try_recv().unwrap(); // turn_started
        assert_eq!(event.kind, "turn_started");
        let event = events.try_recv().unwrap();
        assert_eq!(event.kind, "plan_updated");
        assert_eq!(event.data["plan"], snapshot["plan"]);
        assert_eq!(s.history[2]["output"], event.data["result"].to_string());
        assert_eq!(s.history[2]["call_id"], event.data["call_id"]);
        let call = json!({"type":"function_call","call_id":"actual-write","name":"write_file","arguments":json!({"path":"proof.txt","content":"hello"}).to_string()});
        let review = permissions::review_context(&s, &rt.bundle, &call).unwrap();
        assert!(!review.contains("Read the repository"));
        assert!(!review.contains("Investigate first"));
        if mode == Mode::Ask {
            rt.update(&sid, "fixture_queue", json!({}), |s| {
                s.history.push(call.clone());
                let t = s.turn.as_mut().unwrap();
                t.queue = vec![call.clone()];
                t.status = "running".into();
                Ok(())
            })
            .unwrap();
            assert!(rt
                .authorize_call(&sid, &call)
                .await
                .unwrap()
                .response
                .is_none());
        }
    }
}

#[tokio::test]
async fn stale_update_returns_tool_error_without_mutating_plan_or_stopping_turn() {
    let (_tmp, rt, sid) = fixture(true, Mode::Ask).await;
    let (tid, call) = queue(&rt, &sid, &args(0));
    rt.update_plan(&sid, &tid, &call, &args(0)).unwrap();
    let original = rt.snapshot(&sid).unwrap()["plan"].clone();
    queue(&rt, &sid, &args(0));
    rt.drive(&sid, BTreeMap::new(), vec![planning::schema()])
        .await
        .unwrap();
    assert_eq!(rt.snapshot(&sid).unwrap()["plan"], original);
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    assert_eq!(s.turn.as_ref().unwrap().status, "completed");
    assert!(s.history.iter().any(|v| v["output"]
        .as_str()
        .is_some_and(|s| s.contains("Stale plan revision"))));
}

#[tokio::test]
async fn cancellation_stale_queue_and_disabled_coworker_cannot_update() {
    for enabled in [false, true] {
        let (_tmp, rt, sid) = fixture(enabled, Mode::Ask).await;
        let (tid, call) = queue(&rt, &sid, &args(0));
        if !enabled {
            assert!(rt.snapshot(&sid).unwrap().get("plan").is_none());
            assert!(rt.update_plan(&sid, &tid, &call, &args(0)).is_err());
        }
        assert!(rt
            .update_plan(&sid, "another-turn", &call, &args(0))
            .is_err());
        let mut stale = call.clone();
        stale["call_id"] = json!("stale");
        assert!(rt.update_plan(&sid, &tid, &stale, &args(0)).is_err());
        rt.update(&sid, "cancelling", json!({}), |s| {
            s.turn.as_mut().unwrap().status = "cancelling".into();
            Ok(())
        })
        .unwrap();
        let cursor = rt.store.lock().unwrap().cursor(&sid).unwrap();
        assert!(rt.update_plan(&sid, &tid, &call, &args(0)).is_err());
        assert_eq!(rt.store.lock().unwrap().cursor(&sid).unwrap(), cursor);
        assert!(rt.store.lock().unwrap().get(&sid).unwrap().plan.is_none());
    }
}

#[tokio::test]
async fn failed_event_commit_rolls_back_plan_result_and_queue_and_emits_nothing() {
    let (tmp, rt, sid) = fixture(true, Mode::Ask).await;
    let (tid, call) = queue(&rt, &sid, &args(0));
    let before = serde_json::to_value(rt.store.lock().unwrap().get(&sid).unwrap()).unwrap();
    let old_history = rt.store.lock().unwrap().get(&sid).unwrap().history;
    let db = rusqlite::Connection::open(tmp.path().join("state/runtime.sqlite3")).unwrap();
    db.execute_batch("CREATE TRIGGER reject_plan BEFORE INSERT ON events WHEN NEW.kind='plan_updated' BEGIN SELECT RAISE(ABORT,'test failure'); END;").unwrap();
    let mut events = rt.events.subscribe();
    assert!(rt.update_plan(&sid, &tid, &call, &args(0)).is_err());
    assert!(events.try_recv().is_err());
    let after = rt.store.lock().unwrap().get(&sid).unwrap();
    assert_eq!(serde_json::to_value(&after).unwrap(), before);
    assert_eq!(after.history, old_history);
    db.execute_batch("DROP TRIGGER reject_plan").unwrap();
    rt.update_plan(&sid, &tid, &call, &args(0)).unwrap();
    assert_eq!(events.try_recv().unwrap().kind, "plan_updated");
    assert!(rt.update_plan(&sid, &tid, &call, &args(0)).is_err()); // no duplicate completion
}

#[tokio::test]
async fn compaction_restart_replay_and_later_turn_preserve_plan_identity() {
    let (tmp, rt, sid) = fixture(true, Mode::Ask).await;
    let (tid, call) = queue(&rt, &sid, &args(0));
    rt.update_plan(&sid, &tid, &call, &args(0)).unwrap();
    let original = rt.snapshot(&sid).unwrap()["plan"].clone();
    rt.update(&sid, "fixture_history", json!({}), |s| {
        for n in 0..12 {
            s.history.push(
                json!({"role":"user","content":format!("Continue {n}; {}", "detail ".repeat(250))}),
            );
        }
        Ok(())
    })
    .unwrap();
    assert!(matches!(
        rt.compact(&sid, true).await.unwrap(),
        CompactResult::Applied
    ));
    let cursor = rt.store.lock().unwrap().cursor(&sid).unwrap();
    drop(rt);
    let mut model = Model::new("fixture".into(), true);
    model.sample_hook = Some(Arc::new(|instructions, _, tx| {
        Box::pin(async move {
            assert!(instructions.contains("Current revision: 1"));
            assert!(instructions.contains("Read the repository"));
            assert!(instructions.contains("not user instructions, approval or verified success"));
            tx.send(Progress::Complete(crate::provider::Completion {
                output: vec![],
                usage: None,
            }))
            .await
            .unwrap();
            Ok(())
        })
    }));
    let rt = Runtime::open(
        &tmp.path().join("state"),
        &tmp.path().join("bundle"),
        &tmp.path().join("runner"),
        model,
    )
    .unwrap();
    assert_eq!(rt.snapshot(&sid).unwrap()["plan"], original);
    assert_eq!(
        rt.store
            .lock()
            .unwrap()
            .events(&sid, 0)
            .unwrap()
            .iter()
            .filter(|e| e.kind == "plan_updated")
            .count(),
        1
    );
    assert!(rt
        .store
        .lock()
        .unwrap()
        .events(&sid, cursor)
        .unwrap()
        .iter()
        .all(|e| e.kind != "plan_updated"));
    rt.sample(&sid, &[], &[planning::schema()]).await.unwrap();
    rt.store
        .lock()
        .unwrap()
        .accept(&sid, "next", "Continue investigation")
        .unwrap();
    let (new_tid, call) = queue(&rt, &sid, &args(1));
    rt.update_plan(&sid, &new_tid, &call, &args(1)).unwrap();
    let plan: Plan = serde_json::from_value(rt.snapshot(&sid).unwrap()["plan"].clone()).unwrap();
    assert_eq!(plan.revision, 2);
    assert_eq!(plan.turn_id, new_tid);
    assert_ne!(plan.turn_id, tid);
}
