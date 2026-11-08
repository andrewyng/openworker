use super::*;
use crate::safety::{Action, Config, Guardrails, Rule};
use std::sync::atomic::AtomicUsize;

fn message(text: &str) -> Value {
    json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":text}]})
}
async fn fixture(config: Config, model: Model) -> (tempfile::TempDir, Arc<Runtime>, String) {
    let tmp = tempfile::tempdir().unwrap();
    let work = tmp.path().join("work");
    std::fs::create_dir(&work).unwrap();
    let runner = tmp.path().join("runner");
    std::fs::write(&runner, b"\x7fELF-not-executed").unwrap();
    let bundle = Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let rt = Runtime::open(&tmp.path().join("state"), &bundle, &runner, model).unwrap();
    let sid=rt.dispatch("session/create",json!({"name":"Safety","coworker":"assistant-example","workspace":work,"safety":config,"compaction":{"enabled":false},"permissions":{"mode":"bypass"}})).await.unwrap()["session"]["id"].as_str().unwrap().to_owned();
    (tmp, rt, sid)
}
fn fake(outputs: Vec<&'static str>, seen: Arc<AtomicUsize>) -> Model {
    let mut m = Model::new("test".into(), true);
    m.sample_hook = Some(Arc::new(move |_, history, tx| {
        let n = seen.fetch_add(1, Ordering::SeqCst);
        let text = outputs[n.min(outputs.len() - 1)];
        assert!(!serde_json::to_string(&history)
            .unwrap()
            .contains("SECRET_SENTINEL"));
        Box::pin(async move {
            tx.send(Progress::Delta(text.into())).await.unwrap();
            tx.send(Progress::Complete(crate::provider::Completion {
                output: vec![message(text)],
                usage: Some(crate::provider::Usage {
                    output_tokens: 10,
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
fn config(action: Action) -> Config {
    Config {
        guardrails: Guardrails {
            user_input: vec![Rule::regex("input-secret", "SECRET_[A-Z]+", action.clone())],
            model_output: vec![Rule::regex("output-secret", "SECRET_[A-Z]+", action)],
            ..Default::default()
        },
        ..Default::default()
    }
}
fn no_leak(rt: &Runtime, sid: &str) {
    let snapshot = rt.snapshot(sid).unwrap();
    let events = {
        let db = rt.store.lock().unwrap();
        json!(db.events(sid, 0).unwrap())
    };
    let history = {
        let db = rt.store.lock().unwrap();
        db.history_page(sid, 0).unwrap()
    };
    for data in [snapshot, events, history] {
        assert!(
            !data.to_string().contains("SECRET_SENTINEL"),
            "public sentinel leak"
        );
    }
}

#[tokio::test]
async fn user_block_precedes_admission_and_never_enters_history() {
    let seen = Arc::new(AtomicUsize::new(0));
    let (_tmp, rt, sid) = fixture(config(Action::Block), fake(vec!["safe"], seen.clone())).await;
    let out = rt
        .dispatch(
            "turn/start",
            json!({"session_id":sid,"submission_id":"blocked","text":"SECRET_SENTINEL"}),
        )
        .await;
    assert!(out.is_err());
    assert_eq!(seen.load(Ordering::SeqCst), 0);
    assert!(!rt
        .store
        .lock()
        .unwrap()
        .has_submission(&sid, "blocked")
        .unwrap());
    no_leak(&rt, &sid);
}

#[tokio::test]
async fn guarded_output_redacts_before_deltas_snapshots_and_history() {
    let seen = Arc::new(AtomicUsize::new(0));
    let (_tmp, rt, sid) = fixture(
        config(Action::redact()),
        fake(vec!["SECRET_SENTINEL"], seen.clone()),
    )
    .await;
    let input = rt.check_user_input(&sid, "SECRET_SENTINEL").await.unwrap();
    assert_eq!(input, "[redacted]");
    rt.store
        .lock()
        .unwrap()
        .accept(&sid, "redacted", &input)
        .unwrap();
    rt.drive(&sid, Default::default(), vec![]).await.unwrap();
    no_leak(&rt, &sid);
    assert_eq!(seen.load(Ordering::SeqCst), 1);
    assert!(rt
        .store
        .lock()
        .unwrap()
        .history_page(&sid, 0)
        .unwrap()
        .to_string()
        .contains("[redacted]"));
}

#[tokio::test]
async fn blocked_output_regenerates_from_accepted_history_with_bounded_budget() {
    let seen = Arc::new(AtomicUsize::new(0));
    let (_tmp, rt, sid) = fixture(
        config(Action::Block),
        fake(vec!["SECRET_SENTINEL", "Safe answer"], seen.clone()),
    )
    .await;
    rt.store
        .lock()
        .unwrap()
        .accept(&sid, "retry", "hello")
        .unwrap();
    rt.drive(&sid, Default::default(), vec![]).await.unwrap();
    assert_eq!(seen.load(Ordering::SeqCst), 2);
    no_leak(&rt, &sid);
    let s = rt.store.lock().unwrap().metadata(&sid).unwrap();
    let t = s.turn.unwrap();
    assert_eq!(t.safety_retries, 1);
    assert_eq!(t.budget.model_calls, 2);
}

#[tokio::test]
async fn persistent_output_block_stops_after_configured_retry() {
    let seen = Arc::new(AtomicUsize::new(0));
    let (_tmp, rt, sid) = fixture(
        config(Action::Block),
        fake(vec!["SECRET_SENTINEL"], seen.clone()),
    )
    .await;
    rt.store
        .lock()
        .unwrap()
        .accept(&sid, "retry", "hello")
        .unwrap();
    assert!(rt
        .drive(&sid, Default::default(), vec![])
        .await
        .unwrap_err()
        .is::<safety::Blocked>());
    assert_eq!(seen.load(Ordering::SeqCst), 2);
    no_leak(&rt, &sid);
}

struct Deny;
impl crate::safety::Handler for Deny {
    fn call(
        &self,
        _: crate::safety::Context,
        _: CancellationToken,
    ) -> crate::safety::CheckFuture<'_> {
        Box::pin(async { Ok(crate::safety::Decision::block("PUBLICATION_BLOCKED")) })
    }
}

#[tokio::test]
async fn application_instruction_addendum_is_checked_before_the_model() {
    use crate::safety::{HandlerRef, Hook, HookPhase};
    struct Inspect;
    impl crate::safety::Handler for Inspect {
        fn call(
            &self,
            c: crate::safety::Context,
            _: CancellationToken,
        ) -> crate::safety::CheckFuture<'_> {
            Box::pin(async move {
                assert_eq!(c.phase, crate::safety::Phase::ModelInput);
                assert!(c.text.contains("BLOCKED_APPLICATION_CONTEXT"));
                Ok(crate::safety::Decision::block(
                    "APPLICATION_CONTEXT_BLOCKED",
                ))
            })
        }
    }
    let reference = HandlerRef {
        name: "inspect-context".into(),
        version: "1".into(),
    };
    let config = Config {
        hooks: vec![Hook {
            name: "inspect-context".into(),
            version: "1".into(),
            when: HookPhase::BeforeModel,
            tools: vec![],
            handler: reference.clone(),
            timeout_ms: 100,
        }],
        ..Default::default()
    };
    let seen = Arc::new(AtomicUsize::new(0));
    let (_tmp, rt, sid) = fixture(config, fake(vec!["must not run"], seen.clone())).await;
    rt.safety_handlers
        .register(reference, Arc::new(Inspect))
        .unwrap();
    rt.store
        .lock()
        .unwrap()
        .accept_with_context(
            &sid,
            "context",
            "hello",
            None,
            None,
            crate::turn_options::Context {
                instruction_addendum: Some("BLOCKED_APPLICATION_CONTEXT".into()),
                ..Default::default()
            },
        )
        .unwrap();
    let history = rt.store.lock().unwrap().get(&sid).unwrap().history;
    assert!(rt
        .sample(&sid, &history, &[])
        .await
        .unwrap_err()
        .is::<super::safety::Blocked>());
    assert_eq!(seen.load(Ordering::SeqCst), 0);
    assert!(rt.store.lock().unwrap().attempts(&sid).unwrap().is_empty());
    assert!(!rt
        .store
        .lock()
        .unwrap()
        .history_page(&sid, 0)
        .unwrap()
        .to_string()
        .contains("BLOCKED_APPLICATION_CONTEXT"));
}
#[tokio::test]
async fn hook_blocks_tool_in_bypass_and_returns_safe_unexecuted_result_to_model() {
    use crate::safety::{HandlerRef, Hook, HookPhase};
    let mut model = Model::new("test".into(), true);
    let count = Arc::new(AtomicUsize::new(0));
    let seen = count.clone();
    model.sample_hook = Some(Arc::new(move |_, history, tx| {
        let n = seen.fetch_add(1, Ordering::SeqCst);
        let output = if n == 0 {
            json!({"type":"function_call","name":"write_file","call_id":"blocked-call","arguments":json!({"path":"no-write.txt","content":"SECRET_SENTINEL"}).to_string()})
        } else {
            let result = history
                .iter()
                .find(|v| v["type"] == "function_call_output")
                .unwrap();
            let result: Value = serde_json::from_str(result["output"].as_str().unwrap()).unwrap();
            assert_eq!(result["executed"], false);
            assert!(!serde_json::to_string(&history)
                .unwrap()
                .contains("SECRET_SENTINEL"));
            message("I could not write the file.")
        };
        Box::pin(async move {
            tx.send(Progress::Complete(crate::provider::Completion {
                output: vec![output],
                usage: Some(crate::provider::Usage {
                    output_tokens: 10,
                    ..Default::default()
                }),
            }))
            .await
            .unwrap();
            Ok(())
        })
    }));
    let reference = HandlerRef {
        name: "deny".into(),
        version: "1".into(),
    };
    let config = Config {
        hooks: vec![Hook {
            name: "deny".into(),
            version: "1".into(),
            when: HookPhase::BeforeTool,
            tools: vec![],
            handler: reference.clone(),
            timeout_ms: 50,
        }],
        ..Default::default()
    };
    let (tmp, rt, sid) = fixture(config, model).await;
    rt.safety_handlers
        .register(reference, Arc::new(Deny))
        .unwrap();
    rt.store
        .lock()
        .unwrap()
        .accept(&sid, "blocked-tool", "write a file")
        .unwrap();
    let schema = json!({"type":"function","name":"write_file","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}});
    rt.drive(&sid, Default::default(), vec![schema])
        .await
        .unwrap();
    assert_eq!(count.load(Ordering::SeqCst), 2);
    assert!(!tmp.path().join("work/no-write.txt").exists());
    no_leak(&rt, &sid);
    assert!(!rt
        .store
        .lock()
        .unwrap()
        .events(&sid, 0)
        .unwrap()
        .iter()
        .any(|e| e.kind == "tool_started"));
}

#[tokio::test]
async fn blocked_result_reports_completed_effect_without_exposing_result() {
    use crate::safety::{HandlerRef, Hook, HookPhase};
    let reference = HandlerRef {
        name: "deny".into(),
        version: "1".into(),
    };
    let config = Config {
        hooks: vec![Hook {
            name: "deny".into(),
            version: "1".into(),
            when: HookPhase::AfterTool,
            tools: vec![],
            handler: reference.clone(),
            timeout_ms: 50,
        }],
        ..Default::default()
    };
    let (_tmp, rt, sid) = fixture(config, Model::new("test".into(), true)).await;
    rt.safety_handlers
        .register(reference, Arc::new(Deny))
        .unwrap();
    let result = rt
        .check_tool_result(
            &sid,
            "read_file",
            json!({"content":[{"type":"text","text":"SECRET_SENTINEL"}]}),
        )
        .await
        .unwrap();
    assert_eq!(result["executed"], true);
    assert_eq!(result["outputBlocked"], true);
    assert!(!result.to_string().contains("SECRET_SENTINEL"));
    no_leak(&rt, &sid);
}

#[tokio::test]
async fn transactional_memory_result_is_held_until_inspection_and_recovery_never_replays() {
    use crate::safety::{HandlerRef, Hook, HookPhase};
    let reference = HandlerRef {
        name: "deny".into(),
        version: "1".into(),
    };
    let config = Config {
        hooks: vec![Hook {
            name: "deny".into(),
            version: "1".into(),
            when: HookPhase::AfterTool,
            tools: vec![],
            handler: reference.clone(),
            timeout_ms: 50,
        }],
        ..Default::default()
    };
    let (tmp, rt, sid) = fixture(config, Model::new("test".into(), true)).await;
    rt.safety_handlers
        .register(reference, Arc::new(Deny))
        .unwrap();
    rt.store
        .lock()
        .unwrap()
        .accept(&sid, "memory", "read memory")
        .unwrap();
    let call = json!({"type":"function_call","call_id":"memory-call","name":"memory_read","arguments":"{\"id\":\"m1\"}"});
    let session = rt
        .update(&sid, "fixture", json!({}), |s| {
            s.package.memory = true;
            s.history.push(call.clone());
            let t = s.turn.as_mut().unwrap();
            t.status = "running".into();
            t.queue = vec![call.clone()];
            Ok(())
        })
        .unwrap();
    let scope = crate::assistant_tools::scope(&session);
    let tid = session.turn.unwrap().id;
    let mut bank = crate::assistant_tools::MemoryBank::default();
    bank.entries.insert(
        "m1".into(),
        crate::assistant_tools::Memory {
            id: "m1".into(),
            revision: 1,
            summary: "test".into(),
            content: "SECRET_SENTINEL".into(),
            updated_at: 1,
        },
    );
    let db = rusqlite::Connection::open(tmp.path().join("state/runtime.sqlite3")).unwrap();
    db.execute(
        "INSERT INTO assistant_memories VALUES(?1,?2)",
        rusqlite::params![scope, serde_json::to_string(&bank).unwrap()],
    )
    .unwrap();
    drop(db);
    let candidate = rt
        .store
        .lock()
        .unwrap()
        .assistant_call(&sid, &tid, &call, &json!({"id":"m1"}))
        .unwrap();
    assert!(candidate.data.to_string().contains("SECRET_SENTINEL"));
    no_leak(&rt, &sid);
    assert!(rt
        .store
        .lock()
        .unwrap()
        .metadata(&sid)
        .unwrap()
        .turn
        .unwrap()
        .pending_safety_result
        .is_some());
    let (held, result) = rt
        .release_internal_result(&sid, &call, candidate.data["result"].clone())
        .await
        .unwrap();
    assert!(held);
    assert_eq!(result["outputBlocked"], true);
    no_leak(&rt, &sid);
    let call = json!({"type":"function_call","call_id":"memory-crash","name":"memory_read","arguments":"{\"id\":\"m1\"}"});
    rt.update(&sid, "fixture", json!({}), |s| {
        s.history.push(call.clone());
        s.turn.as_mut().unwrap().queue = vec![call.clone()];
        Ok(())
    })
    .unwrap();
    rt.store
        .lock()
        .unwrap()
        .assistant_call(&sid, &tid, &call, &json!({"id":"m1"}))
        .unwrap();
    rt.store.lock().unwrap().recover().unwrap();
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    let t = s.turn.unwrap();
    assert!(t.pending_safety_result.is_none() && t.queue.is_empty());
    let output: Value =
        serde_json::from_str(s.history.last().unwrap()["output"].as_str().unwrap()).unwrap();
    assert_eq!(output["executed"], true);
    assert_eq!(output["code"], "INSPECTION_INTERRUPTED");
    no_leak(&rt, &sid);
}

#[tokio::test]
async fn turn_overrides_bind_idempotency_and_do_not_leak_to_the_next_turn() {
    let (_tmp, rt, sid) = fixture(Config::default(), Model::new("test".into(), true)).await;
    let rules = config(Action::Block);
    assert!(rt.dispatch("turn/start",json!({"session_id":sid,"submission_id":"rejected","text":"SECRET_SENTINEL","safety":rules})).await.is_err());
    assert!(!rt
        .store
        .lock()
        .unwrap()
        .has_submission(&sid, "rejected")
        .unwrap());
    let (first, fresh, _) = rt
        .store
        .lock()
        .unwrap()
        .accept_with_safety(&sid, "bound", "safe", None, Some(rules.clone()))
        .unwrap();
    assert!(fresh);
    let (second, fresh, _) = rt
        .store
        .lock()
        .unwrap()
        .accept_with_safety(&sid, "bound", "safe", None, Some(rules.clone()))
        .unwrap();
    assert!(!fresh);
    assert_eq!(first.turn.unwrap().id, second.turn.unwrap().id);
    assert!(rt
        .store
        .lock()
        .unwrap()
        .accept_with_safety(&sid, "bound", "safe", None, None)
        .is_err());
    rt.update(&sid, "turn_completed", json!({"status":"completed"}), |s| {
        s.turn.as_mut().unwrap().status = "completed".into();
        Ok(())
    })
    .unwrap();
    let (next, _, _) = rt
        .store
        .lock()
        .unwrap()
        .accept_with_safety(&sid, "next", "safe", None, None)
        .unwrap();
    assert_eq!(next.turn.unwrap().safety, Config::default());
    no_leak(&rt, &sid);
}

#[tokio::test]
async fn before_model_rejection_has_no_attempt_or_working_model_charge() {
    use crate::safety::{HandlerRef, Hook, HookPhase};
    let reference = HandlerRef {
        name: "deny".into(),
        version: "1".into(),
    };
    let config = Config {
        hooks: vec![Hook {
            name: "deny".into(),
            version: "1".into(),
            when: HookPhase::BeforeModel,
            tools: vec![],
            handler: reference.clone(),
            timeout_ms: 50,
        }],
        ..Default::default()
    };
    let seen = Arc::new(AtomicUsize::new(0));
    let (_tmp, rt, sid) = fixture(config, fake(vec!["should not run"], seen.clone())).await;
    rt.safety_handlers
        .register(reference, Arc::new(Deny))
        .unwrap();
    rt.store
        .lock()
        .unwrap()
        .accept(&sid, "blocked-model", "hello")
        .unwrap();
    let history = rt.store.lock().unwrap().get(&sid).unwrap().history;
    assert!(rt
        .sample(&sid, &history, &[])
        .await
        .unwrap_err()
        .is::<super::safety::Blocked>());
    let db = rt.store.lock().unwrap();
    assert!(db.attempts(&sid).unwrap().is_empty());
    let turn = db.metadata(&sid).unwrap().turn.unwrap();
    assert_eq!(turn.budget.model_calls, 0);
    assert_eq!(turn.budget.charged_output_tokens, 0);
    assert_eq!(seen.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn observer_outbox_is_metadata_only_and_does_not_hold_the_agent_turn() {
    use crate::safety::{CheckFuture, Context, Decision, Handler, HandlerRef, Hook, HookPhase};
    struct Observe(Arc<Mutex<Vec<Value>>>);
    impl Handler for Observe {
        fn call(&self, c: Context, _: CancellationToken) -> CheckFuture<'_> {
            Box::pin(async move {
                self.0.lock().unwrap().push(c.context);
                Ok(Decision::Allow)
            })
        }
    }
    let reference = HandlerRef {
        name: "activity".into(),
        version: "1".into(),
    };
    let config = Config {
        hooks: vec![Hook {
            name: "activity".into(),
            version: "1".into(),
            when: HookPhase::OnEvent,
            tools: vec![],
            handler: reference.clone(),
            timeout_ms: 100,
        }],
        ..Default::default()
    };
    let received = Arc::new(Mutex::new(vec![]));
    let (_tmp, rt, sid) = fixture(config, Model::new("test".into(), true)).await;
    rt.safety_handlers
        .register(reference, Arc::new(Observe(received.clone())))
        .unwrap();
    rt.start_observers();
    rt.progress(
        &sid,
        "safety_test",
        json!({"text":"SECRET_SENTINEL","code":"DEMO"}),
    )
    .unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        loop {
            if received
                .lock()
                .unwrap()
                .iter()
                .any(|v| v["event"]["kind"] == "safety_test")
            {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let records = received.lock().unwrap().clone();
    assert!(!json!(records).to_string().contains("SECRET_SENTINEL"));
    assert!(records.iter().all(|v| v["eventId"]
        .as_str()
        .unwrap()
        .starts_with(&format!("{sid}:"))));
    rt.shutdown().await.unwrap();
}

#[tokio::test]
async fn guard_model_binding_accepts_key_rotation_but_not_provider_retargeting() {
    use crate::safety::{Detector, ModelRef};
    let (_tmp, rt, sid) = fixture(Config::default(), Model::new("test".into(), true)).await;
    rt.connections
        .register_host(
            "privacy",
            crate::provider::Kind::Openai,
            "privacy-endpoint-v1",
            "fake-one".to_owned().into(),
        )
        .unwrap();
    let config = Config {
        guardrails: Guardrails {
            user_input: vec![Rule {
                name: "spoken-pii".into(),
                version: "1".into(),
                detector: Detector::Model {
                    model: ModelRef::new("privacy", "fixture"),
                    instructions: "Find identifiers".into(),
                },
                action: Action::Block,
                timeout_ms: 100,
            }],
            ..Default::default()
        },
        ..Default::default()
    };
    let pins = rt.pin_safety_models(&config).unwrap();
    rt.update(&sid, "test_policy", json!({}), |s| {
        s.safety = config;
        s.safety_models = pins;
        Ok(())
    })
    .unwrap();
    let saved = rt.store.lock().unwrap().metadata(&sid).unwrap();
    let pinned = saved.safety_models.values().next().unwrap();
    rt.connections
        .register_host(
            "privacy",
            crate::provider::Kind::Openai,
            "privacy-endpoint-v1",
            "fake-two".to_owned().into(),
        )
        .unwrap();
    assert!(rt.connections.key(pinned).is_ok());
    rt.connections
        .register_host(
            "privacy",
            crate::provider::Kind::Openai,
            "different-endpoint",
            "fake-three".to_owned().into(),
        )
        .unwrap();
    assert!(rt.connections.key(pinned).is_err());
    assert_eq!(
        rt.store
            .lock()
            .unwrap()
            .metadata(&sid)
            .unwrap()
            .safety_models
            .values()
            .next()
            .unwrap()
            .identity,
        pinned.identity
    );
}

#[tokio::test]
async fn tool_rules_resolve_registered_qualified_names_without_cross_namespace_matches() {
    let config = Config {
        guardrails: Guardrails {
            tool_results: vec![crate::safety::ToolRules {
                tools: vec!["example-tools.read_file".into()],
                rules: vec![Rule::regex("secret", "SECRET_[A-Z]+", Action::redact())],
            }],
            ..Default::default()
        },
        ..Default::default()
    };
    let (_tmp, rt, sid) = fixture(config, Model::new("test".into(), true)).await;
    assert_eq!(rt.safety_tool_name("read_file"), "example-tools.read_file");
    assert_eq!(
        rt.safety_tool_name("example-tools.read_file"),
        "example-tools.read_file"
    );
    assert_ne!(
        rt.safety_tool_name("other.read_file"),
        "example-tools.read_file"
    );
    let checked = rt
        .check_tool_result(&sid, "read_file", json!({"text":"SECRET_SENTINEL"}))
        .await
        .unwrap();
    assert!(!checked.to_string().contains("SECRET_SENTINEL"));
    assert!(!rt
        .effective_safety(&sid)
        .unwrap()
        .protects(crate::safety::Phase::ToolResult, Some("other.read_file")));
}
