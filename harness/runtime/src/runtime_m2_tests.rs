use super::*;
use crate::provider::{Completion, Error, Failure};
use std::sync::atomic::AtomicUsize;

#[tokio::test]
async fn live_tool_access_reaches_model_and_refreshes_on_retry_without_entering_history() {
    use openworker_tools::{Tool, ToolContext, ToolDescriptor, ToolFuture};
    struct Access(Arc<AtomicBool>);
    impl Tool for Access {
        fn descriptor(&self) -> ToolDescriptor {
            ToolDescriptor {
                qualified_name: "test.access".into(),
                description: "test".into(),
                version: "1".into(),
                input_schema: json!({"type":"object"}),
            }
        }
        fn call(&self, _: ToolContext, _: Value) -> ToolFuture<'_> {
            Box::pin(async { unreachable!() })
        }
        fn model_context(&self, _: &str) -> Option<Value> {
            Some(json!({"enabled": self.0.load(Ordering::SeqCst)}))
        }
    }
    let enabled = Arc::new(AtomicBool::new(true));
    let calls = Arc::new(AtomicUsize::new(0));
    let (access, count) = (enabled.clone(), calls.clone());
    let mut model = Model::new("test".into(), true);
    model.sample_hook = Some(Arc::new(move |instructions, _, tx| {
        let (access, count) = (access.clone(), count.clone());
        Box::pin(async move {
            assert!(instructions.contains("supersede older access errors"));
            if count.fetch_add(1, Ordering::SeqCst) == 0 {
                assert!(instructions.contains(r#""test.access":{"enabled":true}"#));
                access.store(false, Ordering::SeqCst);
                return Err(Error::new(Failure::Transport).into());
            }
            assert!(instructions.contains(r#""test.access":{"enabled":false}"#));
            tx.send(Progress::Complete(Completion {
                output: vec![],
                usage: None,
            }))
            .await
            .unwrap();
            Ok(())
        })
    }));
    let (_tmp, mut rt, sid) = fixture(model, true).await;
    let bundle = &mut Arc::get_mut(&mut rt).unwrap().bundle;
    bundle.registry.register(Access(enabled)).unwrap();
    bundle.tools.bindings.insert(
        "test__access".into(),
        crate::bundle::Binding {
            server: "test".into(),
            tool: "test.access".into(),
        },
    );
    let old_history = rt.store.lock().unwrap().get(&sid).unwrap().history;
    rt.sample(&sid, &[], &[]).await.unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert_eq!(
        rt.store.lock().unwrap().get(&sid).unwrap().history,
        old_history
    );
}

#[tokio::test]
async fn image_consent_is_rechecked_on_each_provider_retry_without_journaling_pixels() {
    use openworker_tools::{Tool, ToolContext, ToolDescriptor, ToolFuture, ToolImage};
    struct ImageGate(Arc<AtomicBool>);
    impl Tool for ImageGate {
        fn descriptor(&self) -> ToolDescriptor {
            ToolDescriptor {
                qualified_name: "test.image".into(),
                description: "test".into(),
                version: "1".into(),
                input_schema: json!({"type":"object"}),
            }
        }
        fn call(&self, _: ToolContext, _: Value) -> ToolFuture<'_> {
            Box::pin(async { unreachable!() })
        }
        fn images_allowed(&self, _: &str) -> bool {
            self.0.load(Ordering::SeqCst)
        }
    }
    let consent = Arc::new(AtomicBool::new(true));
    let calls = Arc::new(AtomicUsize::new(0));
    let (allowed, count) = (consent.clone(), calls.clone());
    let mut model = Model::new("test".into(), true);
    model.sample_hook = Some(Arc::new(move |_, history, tx| {
        let (allowed, count) = (allowed.clone(), count.clone());
        Box::pin(async move {
            if count.fetch_add(1, Ordering::SeqCst) == 0 {
                assert_eq!(history[0]["output"][1]["type"], "input_image");
                allowed.store(false, Ordering::SeqCst);
                return Err(Error::new(Failure::Transport).into());
            }
            assert!(history[0]["output"]
                .as_str()
                .unwrap()
                .contains("no longer available"));
            tx.send(Progress::Complete(Completion {
                output: vec![],
                usage: None,
            }))
            .await
            .unwrap();
            Ok(())
        })
    }));
    let (_tmp, mut rt, sid) = fixture(model, true).await;
    Arc::get_mut(&mut rt)
        .unwrap()
        .bundle
        .registry
        .register(ImageGate(consent))
        .unwrap();
    let meta = rt
        .images
        .lock()
        .unwrap()
        .put(
            &sid,
            "test.image",
            vec![ToolImage {
                mime_type: "image/jpeg".into(),
                base64: "/9j/2Q==".into(),
            }],
        )
        .unwrap();
    let history = vec![
        json!({"type":"function_call_output","call_id":"test","output":json!({"ephemeral_image":meta}).to_string()}),
    ];
    rt.sample(&sid, &history, &[]).await.unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert!(
        !serde_json::to_string(&rt.store.lock().unwrap().get(&sid).unwrap().history)
            .unwrap()
            .contains("/9j/2Q==")
    );
}

async fn fixture(model: Model, attended: bool) -> (tempfile::TempDir, Arc<Runtime>, String) {
    let tmp = tempfile::tempdir().unwrap();
    let runner = tmp.path().join("runner");
    std::fs::write(&runner, b"\x7fELFtest-only-no-execution").unwrap();
    let workspace = tmp.path().join("workspace");
    std::fs::create_dir(&workspace).unwrap();
    let bundle = Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let rt = Runtime::open(&tmp.path().join("state"), &bundle, &runner, model).unwrap();
    let created=rt.dispatch("session/create",json!({"name":"test","coworker":"assistant-example","workspace":workspace,"compaction":{"cap_tokens":512,"auto_trim_on_failure":!attended}})).await.unwrap();
    let sid = created["session"]["id"].as_str().unwrap().to_string();
    let (mut s, _, _) = rt
        .store
        .lock()
        .unwrap()
        .accept(
            &sid,
            "submit",
            "Original task: preserve the BLUE marker; never deploy anything.",
        )
        .unwrap();
    for n in 0..6 {
        s.history.push(json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"historical detail ".repeat(120)}]}));
        s.history
            .push(json!({"role":"user","content":format!("continue {n}; keep BLUE")}));
    }
    s.turn.as_mut().unwrap().status = "running".into();
    rt.store
        .lock()
        .unwrap()
        .save(&s, "seeded", json!({}))
        .unwrap();
    (tmp, rt, sid)
}

#[tokio::test]
async fn segment_checkpoint_preserves_history_and_rolls_back_failed_event() {
    struct Discard;
    impl crate::telemetry::Sink for Discard {
        fn export(
            &mut self,
            _: &[crate::telemetry::Record],
            _: crate::telemetry::Snapshot,
        ) -> bool {
            true
        }
    }
    let (tmp, mut rt, sid) = fixture(Model::new("fixture".into(), true), true).await;
    let (t, c) = crate::telemetry::Telemetry::start(Discard, 16).unwrap();
    Arc::get_mut(&mut rt).unwrap().telemetry = t;
    let original = rt.store.lock().unwrap().get(&sid).unwrap().history;
    let db = rusqlite::Connection::open(tmp.path().join("state/runtime.sqlite3")).unwrap();
    db.execute_batch("CREATE TRIGGER fail_segment BEFORE INSERT ON events WHEN NEW.kind='execution_segment_started' BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
    assert!(rt.begin_segment(&sid).is_err());
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    assert_eq!(s.history, original);
    assert!(s.turn.unwrap().observation.is_none());
    db.execute_batch("DROP TRIGGER fail_segment").unwrap();
    let (first, turn) = rt.begin_segment(&sid).unwrap();
    let context = first.context();
    first.finish(Outcome::Waiting);
    let (second, same) = rt.begin_segment(&sid).unwrap();
    assert_eq!(turn, same);
    assert_eq!(context.trace, second.context().trace);
    assert_ne!(context.span, second.context().span);
    second.finish(Outcome::Ok);
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    assert_eq!(s.history, original);
    assert_eq!(s.turn.unwrap().observation.unwrap().segments, 2);
    assert!(c.shutdown(std::time::Duration::from_secs(2)));
}

#[tokio::test]
async fn completed_turn_cleanup_cannot_accept_and_strand_the_next_submission() {
    let (_tmp, rt, sid) = fixture(Model::new("fixture".into(), true), true).await;
    rt.update(&sid, "turn_completed", json!({"status":"completed"}), |s| {
        s.turn.as_mut().unwrap().status = "completed".into();
        Ok(())
    })
    .unwrap();
    let before = rt.store.lock().unwrap().get(&sid).unwrap().history;
    // Reproduce the interval between durable completion and old-job cleanup.
    rt.jobs
        .lock()
        .unwrap()
        .insert(sid.clone(), CancellationToken::new());
    let error = rt
        .dispatch(
            "turn/start",
            json!({"session_id":sid,"submission_id":"next","text":"Do the next task"}),
        )
        .await
        .unwrap_err();
    assert!(error.to_string().contains("checkpointing"));
    assert!(!rt
        .store
        .lock()
        .unwrap()
        .has_submission(&sid, "next")
        .unwrap());
    assert_eq!(rt.store.lock().unwrap().get(&sid).unwrap().history, before);
    let duplicate = rt.dispatch("turn/start", json!({"session_id":sid,"submission_id":"submit","text":"Original task: preserve the BLUE marker; never deploy anything."})).await.unwrap();
    assert_eq!(duplicate["duplicate"], true);
    rt.jobs.lock().unwrap().clear();
}

#[tokio::test]
async fn compaction_is_outbound_only_and_survives_restart() {
    let (tmp, rt, sid) = fixture(Model::new("fixture".into(), true), true).await;
    let original = rt.store.lock().unwrap().get(&sid).unwrap().history;
    assert!(matches!(
        rt.compact(&sid, false).await.unwrap(),
        CompactResult::Applied
    ));
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    assert_eq!(s.history, original);
    assert!(s.compaction.as_ref().unwrap().users[0].contains("never deploy"));
    let view = crate::compaction::outbound(&s.history, s.compaction.as_ref());
    assert!(view[0]["content"].as_str().unwrap().contains("BLUE"));
    let boundary = s.compaction.unwrap().boundary;
    let model = rt.model.clone();
    let bundle = rt.bundle.root.clone();
    drop(rt);
    let rt = Runtime::open(
        &tmp.path().join("state"),
        &bundle,
        &tmp.path().join("runner"),
        model,
    )
    .unwrap();
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    assert_eq!(s.history, original);
    assert_eq!(s.compaction.unwrap().boundary, boundary);
    assert_eq!(s.turn.unwrap().status, "interrupted");
}

#[tokio::test]
async fn summary_failures_retry_once_then_park_or_trim() {
    for attended in [true, false] {
        let count = Arc::new(AtomicUsize::new(0));
        let seen = count.clone();
        let mut model = Model::new("fixture".into(), true);
        model.summary_hook = Some(Arc::new(move |_| {
            seen.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { bail!("synthetic failure") })
        }));
        let (_tmp, rt, sid) = fixture(model, attended).await;
        let result = rt.compact(&sid, false).await.unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 2);
        let s = rt.store.lock().unwrap().get(&sid).unwrap();
        if attended {
            assert!(matches!(result, CompactResult::Waiting));
            assert_eq!(s.interactions.len(), 1);
            assert_eq!(s.interactions[0].kind, "compaction");
            assert!(s.compaction.is_none());
            rt.store.lock().unwrap().recover().unwrap();
            let restored = rt.store.lock().unwrap().get(&sid).unwrap();
            assert_eq!(restored.interactions[0].id, s.interactions[0].id);
            assert_eq!(restored.turn.unwrap().status, "recoverable");
        } else {
            assert!(matches!(result, CompactResult::Applied));
            assert!(s.compaction.unwrap().trimmed);
            assert!(s.interactions.is_empty());
        }
    }
}

#[tokio::test]
async fn cancellation_drops_native_summarizer_without_late_commit() {
    let entered = Arc::new(tokio::sync::Notify::new());
    let notify = entered.clone();
    let mut model = Model::new("fixture".into(), true);
    model.summary_hook = Some(Arc::new(move |_| {
        let notify = notify.clone();
        Box::pin(async move {
            notify.notify_one();
            tokio::time::sleep(std::time::Duration::from_secs(60)).await;
            Ok("should never finish".into())
        })
    }));
    let (_tmp, rt, sid) = fixture(model, true).await;
    let token = CancellationToken::new();
    let worker = token.clone();
    let task_rt = rt.clone();
    let task_sid = sid.clone();
    let task = tokio::spawn(async move {
        tokio::select! {biased;
            _=worker.cancelled()=>{},r=task_rt.compact(&task_sid,false)=>{r.unwrap();panic!("summary should have been cancelled");}
        }
    });
    entered.notified().await;
    token.cancel();
    let result = tokio::time::timeout(std::time::Duration::from_millis(200), task).await;
    assert_eq!(
        result.is_err(),
        crate::compaction::DEVIATIONS["rust"]["summary_still_waiting_after_interrupt"]
            .as_bool()
            .unwrap()
    );
    result.unwrap().unwrap();
    assert!(rt
        .store
        .lock()
        .unwrap()
        .get(&sid)
        .unwrap()
        .compaction
        .is_none());
}

#[tokio::test]
async fn stale_summary_cannot_replace_current_history_view() {
    let entered = Arc::new(tokio::sync::Notify::new());
    let release = Arc::new(tokio::sync::Notify::new());
    let mut model = Model::new("fixture".into(), true);
    let e = entered.clone();
    let r = release.clone();
    model.summary_hook = Some(Arc::new(move |_| {
        let e = e.clone();
        let r = r.clone();
        Box::pin(async move {
            e.notify_one();
            r.notified().await;
            Ok(crate::compaction::REFERENCE["cases"]
                .as_array()
                .unwrap()
                .iter()
                .find(|c| c["op"] == "quality" && c["expected"] == true)
                .unwrap()["text"]
                .as_str()
                .unwrap()
                .into())
        })
    }));
    let (_tmp, rt, sid) = fixture(model, true).await;
    let task_rt = rt.clone();
    let task_sid = sid.clone();
    let task = tokio::spawn(async move { task_rt.compact(&task_sid, false).await });
    entered.notified().await;
    rt.update(&sid, "changed", json!({}), |s| {
        s.history
            .push(json!({"role":"user","content":"new instruction"}));
        Ok(())
    })
    .unwrap();
    release.notify_one();
    assert!(task.await.unwrap().is_err());
    assert!(rt
        .store
        .lock()
        .unwrap()
        .get(&sid)
        .unwrap()
        .compaction
        .is_none());
}

#[tokio::test]
async fn retries_keep_partial_attempts_separate_and_do_not_touch_tools_or_history() {
    let count = Arc::new(AtomicUsize::new(0));
    let seen = count.clone();
    let mut model = Model::new("fixture".into(), true);
    model.sample_hook = Some(Arc::new(move |_, _, tx| {
        let n = seen.fetch_add(1, Ordering::SeqCst);
        Box::pin(async move {
            tx.send(Progress::Delta(
                if n == 0 {
                    "failed partial"
                } else {
                    "final answer"
                }
                .into(),
            ))
            .await
            .unwrap();
            tx.send(Progress::Usage(crate::telemetry::TokenUsage {
                input: Some(10),
                output: Some(2),
                ..Default::default()
            }))
            .await
            .unwrap();
            if n == 0 {
                return Err(Error {
                    kind: Failure::RateLimit,
                    retry_after: Some(0),
                    http_status: None,
                }
                .into());
            }
            tx.send(Progress::Complete(Completion{output:vec![json!({"type":"message","content":[{"type":"output_text","text":"final answer"}]})],usage:None})).await.unwrap();
            Ok(())
        })
    }));
    let (_tmp, mut rt, sid) = fixture(model, true).await;
    struct Capture(Arc<Mutex<Vec<crate::telemetry::Record>>>);
    impl crate::telemetry::Sink for Capture {
        fn export(
            &mut self,
            r: &[crate::telemetry::Record],
            _: crate::telemetry::Snapshot,
        ) -> bool {
            self.0.lock().unwrap().extend_from_slice(r);
            true
        }
    }
    let rows = Arc::new(Mutex::new(vec![]));
    let (telemetry, controller) =
        crate::telemetry::Telemetry::start(Capture(rows.clone()), 64).unwrap();
    Arc::get_mut(&mut rt).unwrap().telemetry = telemetry;
    let before = rt.store.lock().unwrap().get(&sid).unwrap().history;
    let result = rt.sample(&sid, &before, &[]).await.unwrap();
    assert!(!serde_json::to_string(&result.output)
        .unwrap()
        .contains("failed partial"));
    assert!(controller.shutdown(std::time::Duration::from_secs(2)));
    let records = rows.lock().unwrap();
    let requests: Vec<_> = records
        .iter()
        .filter(|r| r.operation == Op::Model)
        .collect();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].attempt, Some(1));
    assert_eq!(requests[1].attempt, Some(2));
    assert_eq!(requests[0].context.trace, requests[1].context.trace);
    assert_ne!(requests[0].identity.action, requests[1].identity.action);
    assert_eq!(
        requests
            .iter()
            .map(|r| r.input_tokens.unwrap())
            .sum::<u64>(),
        20
    );
    assert_eq!(requests[0].outcome, Outcome::Error);
    assert_eq!(requests[1].outcome, Outcome::Ok);
    let db = rt.store.lock().unwrap();
    let attempts = db.attempts(&sid).unwrap();
    assert_eq!(attempts.len(), 2);
    assert_eq!(attempts[0]["status"], "completed");
    assert_eq!(attempts[1]["status"], "failed");
    assert_eq!(attempts[1]["text"], "failed partial");
    assert_ne!(attempts[0]["id"], attempts[1]["id"]);
    assert_eq!(db.get(&sid).unwrap().history, before);
    assert!(!db
        .events(&sid, 0)
        .unwrap()
        .iter()
        .any(|e| e.kind == "tool_started"));
}

#[tokio::test]
async fn safety_block_is_durable_and_never_retried() {
    let count = Arc::new(AtomicUsize::new(0));
    let seen = count.clone();
    let mut model = Model::new("fixture".into(), true);
    model.sample_hook = Some(Arc::new(move |_, _, _| {
        seen.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { Err(Error::new(Failure::SafetyBlocked).into()) })
    }));
    let (_tmp, rt, sid) = fixture(model, true).await;
    assert!(rt.sample(&sid, &[], &[]).await.is_err());
    assert_eq!(count.load(Ordering::SeqCst), 1);
    rt.update(&sid, "turn_completed", json!({"status":"failed"}), |s| {
        s.turn.as_mut().unwrap().status = "failed".into();
        Ok(())
    })
    .unwrap();
    assert!(rt.store.lock().unwrap().get(&sid).unwrap().provider_blocked);
    assert!(rt
        .store
        .lock()
        .unwrap()
        .accept(&sid, "again", "retry")
        .is_err());
}

#[tokio::test]
async fn compaction_event_failure_rolls_back_checkpoint() {
    let (tmp, rt, sid) = fixture(Model::new("fixture".into(), true), true).await;
    let db = rusqlite::Connection::open(tmp.path().join("state/runtime.sqlite3")).unwrap();
    db.execute_batch("CREATE TRIGGER fail_compaction BEFORE INSERT ON events WHEN NEW.kind='compacted' BEGIN SELECT RAISE(ABORT,'injected commit fault'); END;").unwrap();
    let before = rt.store.lock().unwrap().get(&sid).unwrap().history;
    assert!(rt.compact(&sid, false).await.is_err());
    let store = rt.store.lock().unwrap();
    assert!(store.get(&sid).unwrap().compaction.is_none());
    assert_eq!(before, store.get(&sid).unwrap().history);
    assert!(!store
        .events(&sid, 0)
        .unwrap()
        .iter()
        .any(|e| e.kind == "compacted"));
}

#[tokio::test]
async fn repeated_transient_failure_stops_after_three_attempts() {
    let count = Arc::new(AtomicUsize::new(0));
    let seen = count.clone();
    let mut model = Model::new("fixture".into(), true);
    model.sample_hook = Some(Arc::new(move |_, _, _| {
        seen.fetch_add(1, Ordering::SeqCst);
        Box::pin(async {
            Err(Error {
                kind: Failure::Transient,
                retry_after: Some(0),
                http_status: None,
            }
            .into())
        })
    }));
    let (_tmp, rt, sid) = fixture(model, true).await;
    assert!(rt.sample(&sid, &[], &[]).await.is_err());
    assert_eq!(count.load(Ordering::SeqCst), 3);
    assert_eq!(
        rt.store
            .lock()
            .unwrap()
            .get(&sid)
            .unwrap()
            .turn
            .unwrap()
            .provider_attempts,
        3
    );
}

#[tokio::test]
async fn retry_backoff_is_cancellable_without_another_request() {
    let count = Arc::new(AtomicUsize::new(0));
    let seen = count.clone();
    let mut model = Model::new("fixture".into(), true);
    model.sample_hook = Some(Arc::new(move |_, _, _| {
        seen.fetch_add(1, Ordering::SeqCst);
        Box::pin(async {
            Err(Error {
                kind: Failure::RateLimit,
                retry_after: Some(30),
                http_status: None,
            }
            .into())
        })
    }));
    let (_tmp, rt, sid) = fixture(model, true).await;
    let mut events = rt.events.subscribe();
    let token = CancellationToken::new();
    let cancel = token.clone();
    let worker = rt.clone();
    let id = sid.clone();
    let task = tokio::spawn(async move {
        tokio::select! {biased;
            _=cancel.cancelled()=>{},r=worker.sample(&id,&[],&[])=>{r.unwrap();panic!("expected cancellation in backoff");}
        }
    });
    tokio::time::timeout(std::time::Duration::from_secs(2), async {
        while events.recv().await.unwrap().kind != "model_retrying" {}
    })
    .await
    .unwrap();
    token.cancel();
    tokio::time::timeout(std::time::Duration::from_millis(200), task)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(count.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn output_limit_continuation_is_bounded_and_not_a_user_instruction() {
    let (_tmp, rt, sid) = fixture(Model::new("fixture".into(), true), true).await;
    assert!(rt.continue_truncated(&sid).unwrap());
    assert!(rt.continue_truncated(&sid).unwrap());
    assert!(!rt.continue_truncated(&sid).unwrap());
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    assert_eq!(s.turn.unwrap().continuations, 2);
    assert_eq!(
        s.history.last().unwrap()["_runtime_notice"],
        "output_continuation"
    );
    assert!(crate::store::terminal("truncated"));
}

#[tokio::test]
async fn compaction_retry_and_pending_approval_survive_the_same_restart_scenario() {
    let count = Arc::new(AtomicUsize::new(0));
    let seen = count.clone();
    let mut model = Model::new("fixture".into(), true);
    model.sample_hook = Some(Arc::new(move |_, history, tx| {
        let n = seen.fetch_add(1, Ordering::SeqCst);
        Box::pin(async move {
            assert!(history[0]["content"].as_str().unwrap().contains("BLUE"));
            if n == 0 {
                tx.send(Progress::Delta("partial before injected failure".into()))
                    .await
                    .unwrap();
                return Err(Error {
                    kind: Failure::Transient,
                    retry_after: Some(0),
                    http_status: None,
                }
                .into());
            }
            tx.send(Progress::Complete(Completion{output:vec![json!({"type":"function_call","call_id":"c_stable","name":"write_file","arguments":"{\"path\":\"proof.txt\",\"content\":\"BLUE\"}"})],usage:None})).await.unwrap();
            Ok(())
        })
    }));
    let (tmp, rt, sid) = fixture(model, true).await;
    assert!(matches!(
        rt.compact(&sid, false).await.unwrap(),
        CompactResult::Applied
    ));
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    let view = crate::compaction::outbound(&s.history, s.compaction.as_ref());
    let completion = rt.sample(&sid, &view, &[]).await.unwrap();
    let call = completion.output[0].clone();
    // Seed the same pre-execution checkpoint used by drive; no tool is executed.
    rt.update(
        &sid,
        "interaction_required",
        json!({"id":"i_stable"}),
        |s| {
            s.history.extend(completion.output);
            let t = s.turn.as_mut().unwrap();
            t.queue = vec![call.clone()];
            t.status = "waiting".into();
            s.interactions.push(Interaction {
                requested_at_ms: None,
                resolved_at_ms: None,
                grant_id: None,
                id: "i_stable".into(),
                turn_id: t.id.clone(),
                kind: "approval".into(),
                call,
                bundle_digest: s.bundle_digest.clone(),
                schema_digest: "test".into(),
                response: None,
                origin: None,
                server: None,
                remote_tool: None,
                policy_digest: s.tool_policy.digest.clone(),
                resource_policy_digest: s.resource_policy.digest.clone(),
            });
            Ok(())
        },
    )
    .unwrap();
    let bundle = rt.bundle.root.clone();
    let model = rt.model.clone();
    drop(rt);
    let rt = Runtime::open(
        &tmp.path().join("state"),
        &bundle,
        &tmp.path().join("runner"),
        model,
    )
    .unwrap();
    let snap = rt.snapshot(&sid).unwrap();
    assert_eq!(snap["turn"]["status"], "recoverable");
    assert_eq!(snap["interactions"][0]["id"], "i_stable");
    assert!(snap["compaction"].is_object());
    assert!(snap["attempts"]
        .to_string()
        .contains("partial before injected failure"));
    assert!(!rt
        .store
        .lock()
        .unwrap()
        .events(&sid, 0)
        .unwrap()
        .iter()
        .any(|e| e.kind == "tool_started"));
}
