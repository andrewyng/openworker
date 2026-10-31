use openworker_runtime::{
    bundle::Bundle,
    model::validate_output,
    runner,
    runtime::validate_arguments,
    sandbox,
    store::{Interaction, Session, Store},
};
use serde_json::{json, Value};

fn session() -> Session {
    Session {
        agent_team: None,
        environment: None,
        storage_revision: 0,
        approval_grants: vec![],
        id: "s_test".into(),
        name: "Test".into(),
        model: "scripted-test".into(),
        model_provider: Default::default(),
        model_selection: None,
        reviewer_selection: None,
        scripted: true,
        workspace: "/workspace".into(),
        coworker: "assistant-example".into(),
        bundle_digest: "bundle".into(),
        schema_digest: Some("schemas".into()),
        history: vec![],
        interactions: vec![],
        turn: None,
        sandbox: None,
        closed: false,
        compaction_config: Default::default(),
        compaction: None,
        last_usage: None,
        provider_blocked: false,
        compaction_action: None,
        permissions: Default::default(),
        execution_backend: Default::default(),
        direct_processes: Default::default(),
        tool_policy: Default::default(),
        package: Default::default(),
        skills: vec![],
        loaded_skills: Default::default(),
        plan: None,
        schedules: vec![],
        resource_policy: Default::default(),
    }
}

#[test]
fn submissions_are_idempotent_and_conflicts_rejected() {
    let tmp = tempfile::tempdir().unwrap();
    let mut db = Store::open(tmp.path()).unwrap();
    db.save(&session(), "created", json!({})).unwrap();
    let (s, fresh, _) = db.accept("s_test", "submit", "Hello").unwrap();
    assert!(fresh);
    let (again, fresh, _) = db.accept("s_test", "submit", "Hello").unwrap();
    assert!(!fresh);
    assert_eq!(s.turn.unwrap().id, again.turn.unwrap().id);
    assert!(db.accept("s_test", "submit", "Different").is_err());
    assert!(db.accept("s_test", "second", "Hello").is_err());
}
#[test]
fn exclusive_state_lock_and_cursor_validation() {
    let tmp = tempfile::tempdir().unwrap();
    let mut db = Store::open(tmp.path()).unwrap();
    assert!(Store::open(tmp.path()).is_err());
    db.save(&session(), "created", json!({})).unwrap();
    assert!(db.events("s_test", -1).is_err());
    assert!(db.events("s_test", 900).is_err());
    assert_eq!(db.events("s_test", 0).unwrap().len(), 1);
}
#[test]
fn recovery_preserves_pending_identity_but_never_replays_executing() {
    for (status, expected) in [
        ("waiting", "recoverable"),
        ("executing", "interrupted"),
        ("accepted", "interrupted"),
        ("cancelling", "interrupted"),
    ] {
        let tmp = tempfile::tempdir().unwrap();
        let mut db = Store::open(tmp.path()).unwrap();
        db.save(&session(), "created", json!({})).unwrap();
        let (mut s, _, _) = db.accept("s_test", "submit", "Do work").unwrap();
        let t = s.turn.as_mut().unwrap();
        t.status = status.into();
        s.interactions.push(Interaction {
            requested_at_ms: None,
            resolved_at_ms: None,
            grant_id: None,
            id: "i_stable".into(),
            turn_id: t.id.clone(),
            kind: "approval".into(),
            call: json!({}),
            bundle_digest: "bundle".into(),
            schema_digest: "schemas".into(),
            response: None,
            origin: None,
            server: None,
            remote_tool: None,
            policy_digest: String::new(),
            resource_policy_digest: String::new(),
        });
        db.save(&s, "checkpoint", json!({})).unwrap();
        drop(db);
        let mut db = Store::open(tmp.path()).unwrap();
        db.recover().unwrap();
        let s = db.get("s_test").unwrap();
        assert_eq!(s.turn.unwrap().status, expected);
        assert_eq!(s.interactions[0].id, "i_stable");
    }
}
#[test]
fn schema_and_call_validation() {
    let schema = json!({"type":"object","properties":{"path":{"type":"string"}},"required":["path"],"additionalProperties":false});
    assert!(validate_arguments(&schema, &json!({"path":"file.txt"})).is_ok());
    for args in [
        json!({}),
        json!({"path":4}),
        json!({"path":"a","injected":true}),
    ] {
        assert!(validate_arguments(&schema, &args).is_err());
    }
    assert!(validate_output(&[
        json!({"type":"function_call","call_id":"c1","name":"write_file","arguments":"invalid"})
    ])
    .is_err());
}
#[test]
fn bundle_identity_and_exact_routing() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let b = Bundle::load(&root).unwrap();
    assert_eq!(b.id, "assistant-example");
    assert_eq!(b.tools.bindings["write_file"].server, "example-tools");
    b.verify().unwrap();
}
#[test]
fn tool_path_rejects_escape_and_symlinks() {
    let temp = tempfile::tempdir().unwrap();
    for p in ["../secret", "/etc/passwd", "a/../../secret", ""] {
        assert!(runner::path(temp.path(), p).is_err());
    }
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink("/tmp", temp.path().join("link")).unwrap();
        assert!(runner::path(temp.path(), "link/file").is_err());
    }
}
#[tokio::test]
async fn native_mcp_file_tools() {
    let temp = tempfile::tempdir().unwrap();
    runner::call(
        temp.path(),
        "write_file",
        &json!({"path":"note.txt","content":"native"}),
    )
    .await
    .unwrap();
    let r = runner::call(temp.path(), "read_file", &json!({"path":"note.txt"}))
        .await
        .unwrap();
    assert_eq!(r["text"], "native");
    assert!(runner::call(
        temp.path(),
        "write_file",
        &json!({"path":"../bad","content":"no"})
    )
    .await
    .is_err());
}
#[test]
fn policy_is_fail_closed() {
    let p = sandbox::policy("/workspace");
    assert_eq!(p["landlock"]["compatibility"], "hard_requirement");
    assert_eq!(p["network_policies"], json!({}));
    assert_eq!(p["filesystem_policy"]["include_workdir"], false);
    assert!(p["filesystem_policy"]["read_write"]
        .as_array()
        .unwrap()
        .contains(&json!("/workspace")));
    assert!(!p.to_string().contains("docker.sock"));
    assert!(!p.to_string().contains("API_KEY"));
}

#[test]
fn replay_pages_bound_bytes_and_keep_cursor_continuity() {
    let dir = tempfile::tempdir().unwrap();
    let mut db = Store::open(dir.path()).unwrap();
    let s = session();
    for _ in 0..8 {
        db.save(&s, "tool_result", json!({"text":"x".repeat(100000)}))
            .unwrap();
    }
    let first = db.events(&s.id, 0).unwrap();
    assert_eq!(first.len(), 5);
    let last = first.last().unwrap().event_id;
    let rest = db.events(&s.id, last).unwrap();
    assert_eq!(rest.len(), 3);
    assert!(rest[0].event_id > last);
}

#[test]
fn old_submission_returns_original_turn_without_changing_active_turn() {
    let dir = tempfile::tempdir().unwrap();
    let mut db = Store::open(dir.path()).unwrap();
    db.save(&session(), "created", json!({})).unwrap();
    let (mut s, _, _) = db.accept("s_test", "first", "One").unwrap();
    let first = s.turn.as_ref().unwrap().id.clone();
    s.turn.as_mut().unwrap().status = "completed".into();
    db.save(&s, "turn_completed", json!({"status":"completed"}))
        .unwrap();
    let (active, _, _) = db.accept("s_test", "second", "Two").unwrap();
    let (old, fresh, _) = db.accept("s_test", "first", "One").unwrap();
    assert!(!fresh);
    assert_eq!(old.turn.unwrap().id, first);
    assert_eq!(
        db.get("s_test").unwrap().turn.unwrap().id,
        active.turn.unwrap().id
    );
}

#[test]
fn schema_checks_nested_constraints_and_refuses_external_references() {
    let schema = json!({"type":"object","properties":{"count":{"type":"integer","minimum":1},"options":{"type":"array","items":{"type":"string"}}},"required":["count","options"]});
    assert!(validate_arguments(&schema, &json!({"count":0,"options":[1]})).is_err());
    assert!(validate_arguments(&schema, &json!({"count":1,"options":["ok"]})).is_ok());
    assert!(validate_arguments(&json!({"$ref":"file:///etc/passwd"}), &json!({})).is_err());
    assert!(validate_arguments(&json!({"$ref":"https://example.com/schema"}), &json!({})).is_err());
}

#[test]
fn incompatible_stores_are_rejected_without_changes() {
    for version in [0, 1, 2, 3, 4, 5, 6, 99] {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runtime.sqlite3");
        let db = rusqlite::Connection::open(&path).unwrap();
        db.execute_batch(&format!("CREATE TABLE sessions(data TEXT); INSERT INTO sessions VALUES('original'); PRAGMA user_version={version};")).unwrap();
        drop(db);
        let before = std::fs::read(&path).unwrap();
        assert!(Store::open(dir.path())
            .err()
            .unwrap()
            .to_string()
            .contains("fresh state"));
        assert_eq!(before, std::fs::read(path).unwrap());
        assert!(!dir.path().join("runtime.lock").exists());
    }
}

#[test]
fn history_is_append_only_and_streaming_does_not_rewrite_sessions() {
    let dir = tempfile::tempdir().unwrap();
    let mut db = Store::open(dir.path()).unwrap();
    let mut s = session();
    s.history
        .push(json!({"role":"user","content":"x".repeat(50_000)}));
    db.save(&s, "created", json!({})).unwrap();
    let inspect = rusqlite::Connection::open(dir.path().join("runtime.sqlite3")).unwrap();
    let meta: String = inspect
        .query_row("SELECT data FROM sessions", [], |r| r.get(0))
        .unwrap();
    assert!(!meta.contains("history"));
    // Configuration may gain bounded fields; the large transcript must remain
    // entirely in the append-only history table, not this JSON row.
    let mut metadata_only = s.clone();
    metadata_only.history.clear();
    assert_eq!(
        serde_json::from_str::<Value>(&meta).unwrap(),
        json!(metadata_only)
    );
    assert!(meta.len() < 2048);
    inspect.execute_batch("CREATE TRIGGER no_metadata_update BEFORE UPDATE ON sessions BEGIN SELECT RAISE(ABORT,'metadata rewritten'); END; CREATE TRIGGER no_history_update BEFORE UPDATE ON history BEGIN SELECT RAISE(ABORT,'history rewritten'); END;").unwrap();
    for _ in 0..100 {
        db.append_event(&s.id, "assistant_delta", json!({"text":"hi"}))
            .unwrap();
    }
    inspect
        .execute_batch("DROP TRIGGER no_metadata_update")
        .unwrap();
    s.history
        .push(json!({"role":"assistant","type":"message","content":[]}));
    db.save(&s, "assistant_message", json!({})).unwrap();
    let before = db.cursor(&s.id).unwrap();
    s.history[0] = json!({"role":"user","content":"rewritten"});
    assert!(db.save(&s, "bad", json!({})).is_err());
    assert_eq!(before, db.cursor(&s.id).unwrap());
    assert_eq!(db.get(&s.id).unwrap().history.len(), 2);
    drop(db);
    assert_eq!(
        Store::open(dir.path())
            .unwrap()
            .get(&s.id)
            .unwrap()
            .history
            .len(),
        2
    );
}

#[test]
fn history_pages_advance_across_private_items_without_disclosing_them() {
    let dir = tempfile::tempdir().unwrap();
    let mut db = Store::open(dir.path()).unwrap();
    let mut s = session();
    for _ in 0..102 {
        s.history
            .push(json!({"type":"reasoning","encrypted_content":"PRIVATE"}));
    }
    s.history.push(json!({"role":"user","content":"visible"}));
    db.save(&s, "created", json!({})).unwrap();
    let first = db.history_page(&s.id, 0).unwrap();
    assert_eq!(first["items"], json!([]));
    assert_eq!(first["cursor"], 100);
    assert_eq!(first["has_more"], true);
    let second = db.history_page(&s.id, 100).unwrap();
    assert_eq!(second["cursor"], 103);
    assert_eq!(second["has_more"], false);
    assert!(!second.to_string().contains("PRIVATE"));
    assert!(second.to_string().contains("visible"));
    assert!(db.history_page(&s.id, 104).is_err());
}

#[test]
fn transcript_excerpts_are_scoped_bounded_and_preserve_unicode() {
    let dir = tempfile::tempdir().unwrap();
    let mut db = Store::open(dir.path()).unwrap();
    let mut s = session();
    s.history
        .push(json!({"role":"user","content":"🌱".repeat(10000)}));
    db.save(&s, "created", json!({})).unwrap();
    let first = db.transcript_excerpt(&s.id, 0, 0).unwrap();
    assert_eq!(first["text"].as_str().unwrap().chars().count(), 8000);
    assert_eq!(first["item_complete"], false);
    let second = db.transcript_excerpt(&s.id, 0, 8000).unwrap();
    assert_eq!(second["item_complete"], true);
    let joined = format!(
        "{}{}",
        first["text"].as_str().unwrap(),
        second["text"].as_str().unwrap()
    );
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&joined).unwrap()["content"],
        "🌱".repeat(10000)
    );
    assert!(db.transcript_excerpt("other", 0, 0).is_err());
    assert!(db.transcript_excerpt(&s.id, 0, 100000).is_err());
}

#[test]
fn cancelled_compaction_answer_cannot_carry_into_a_new_turn() {
    let dir = tempfile::tempdir().unwrap();
    let mut db = Store::open(dir.path()).unwrap();
    let s = session();
    db.save(&s, "created", json!({})).unwrap();
    let (mut s, _, _) = db.accept(&s.id, "first", "first task").unwrap();
    s.compaction_action = Some("Trim oldest 10%".into());
    s.turn.as_mut().unwrap().status = "cancelled".into();
    db.save(&s, "turn_completed", json!({"status":"cancelled"}))
        .unwrap();
    let (next, _, _) = db.accept(&s.id, "next", "new task").unwrap();
    assert!(next.compaction_action.is_none());
}

#[test]
fn resilience_recovery_preserves_budget_cursor_and_reports_unknown_exactly_once() {
    let tmp = tempfile::tempdir().unwrap();
    let mut db = Store::open(tmp.path()).unwrap();
    db.save(&session(), "created", json!({})).unwrap();
    let (mut s, _, _) = db.accept("s_test", "original", "PRIVATE_REQUEST").unwrap();
    let t = s.turn.as_mut().unwrap();
    t.budget.limits.max_model_calls = 9;
    t.budget.model_calls = 3;
    t.budget.charged_output_tokens = 1234;
    t.status = "executing".into();
    let call = json!({"type":"function_call","call_id":"uncertain-write","name":"write_file","arguments":"{\"path\":\"effect\",\"content\":\"PRIVATE_ARGUMENT\"}"});
    t.queue = vec![call.clone()];
    s.history.push(call);
    let before = db
        .save(&s, "tool_started", json!({"call_id":"uncertain-write"}))
        .unwrap()
        .event_id;
    drop(db);
    for _ in 0..2 {
        let mut db = Store::open(tmp.path()).unwrap();
        db.recover().unwrap();
        let s = db.get("s_test").unwrap();
        let t = s.turn.unwrap();
        assert_eq!(t.status, "interrupted");
        assert!(t.queue.is_empty());
        assert_eq!(t.budget.model_calls, 3);
        assert_eq!(t.budget.limits.max_model_calls, 9);
        assert_eq!(t.budget.charged_output_tokens, 1234);
        assert_eq!(
            s.history
                .iter()
                .filter(|v| v["type"] == "function_call_output"
                    && v.to_string().contains("tool_outcome_unknown"))
                .count(),
            1
        );
        let tail = db.events("s_test", before).unwrap();
        assert!(!tail.iter().any(|e| e.kind == "tool_started"));
        assert!(tail.windows(2).all(|w| w[0].event_id < w[1].event_id));
    }
}

#[test]
fn resilience_recovery_distinguishes_unstarted_and_already_completed_calls() {
    for completed in [false, true] {
        let tmp = tempfile::tempdir().unwrap();
        let mut db = Store::open(tmp.path()).unwrap();
        db.save(&session(), "created", json!({})).unwrap();
        let (mut s, _, _) = db.accept("s_test", "original", "Inspect").unwrap();
        let call = json!({"type":"function_call","call_id":"bounded-call","name":"read_file","arguments":"{}"});
        s.turn.as_mut().unwrap().status = "executing".into();
        s.turn.as_mut().unwrap().queue = vec![call.clone()];
        s.history.push(call);
        if completed {
            s.history.push(json!({"type":"function_call_output","call_id":"bounded-call","output":"durable result"}));
        }
        // No tool_started event: queued does not imply dispatched. Conversely a
        // saved result takes precedence and must never be replaced with unknown.
        db.save(&s, "checkpoint", json!({})).unwrap();
        db.recover().unwrap();
        let s = db.get("s_test").unwrap();
        assert_eq!(s.turn.as_ref().unwrap().status, "interrupted");
        assert_eq!(
            s.turn.as_ref().unwrap().queue.len(),
            usize::from(!completed)
        );
        assert!(!s
            .history
            .iter()
            .any(|v| v.to_string().contains("tool_outcome_unknown")));
    }
}
