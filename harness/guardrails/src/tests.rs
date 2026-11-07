use super::*;
use serde_json::json;
use std::sync::atomic::{AtomicUsize, Ordering};

fn context(text: &str, phase: Phase) -> Context {
    Context {
        call_id: None,
        evaluation_id: "evaluation-1".into(),
        session_id: "session-1".into(),
        turn_id: Some("turn-1".into()),
        phase,
        text: text.into(),
        tool: None,
        arguments: None,
        matches: vec![],
        context: Value::Null,
    }
}
fn config(rules: Vec<Rule>) -> Config {
    Config {
        guardrails: Guardrails {
            user_input: rules,
            ..Default::default()
        },
        ..Default::default()
    }
}
async fn evaluate(c: &Config, r: &Registry, text: &str) -> Evaluation {
    apply(
        c,
        r,
        None,
        context(text, Phase::UserInput),
        CancellationToken::new(),
    )
    .await
    .unwrap()
}
struct HandlerFn<F>(F);
impl<F> Handler for HandlerFn<F>
where
    F: Fn(Context) -> Result<Decision> + Send + Sync,
{
    fn call(&self, c: Context, _: CancellationToken) -> CheckFuture<'_> {
        Box::pin(async move { (self.0)(c) })
    }
}
fn handler_ref() -> HandlerRef {
    HandlerRef {
        name: "test.callback".into(),
        version: "1".into(),
    }
}
fn registered(f: impl Fn(Context) -> Result<Decision> + Send + Sync + 'static) -> Registry {
    let r = Registry::default();
    r.register(handler_ref(), Arc::new(HandlerFn(f))).unwrap();
    r
}
fn callback() -> Action {
    Action::Callback {
        handler: handler_ref(),
    }
}

#[test]
fn wire_names_and_bounds_are_explicit() {
    let c = config(vec![Rule::credit_card(Action::Redact {
        replacement: "[card]".into(),
        keep_last: 4,
    })]);
    let value = serde_json::to_value(c).unwrap();
    assert_eq!(value["guardrails"]["userInput"][0]["action"]["keepLast"], 4);
    assert_eq!(serde_json::to_value(Decision::block("TEST")).unwrap()["feedbackToModel"],"This action or content is blocked by application policy. Choose an allowed alternative without sensitive information.");
    let c: Config =
        serde_json::from_value(json!({"blockedOutputOptions":{"maxRetries":2}})).unwrap();
    c.validate().unwrap();
    assert!(serde_json::from_value::<Config>(json!({"blockedOutput":{"maxRetries":2}})).is_err());
    for pattern in ["", "a*", "(?=secret)", "(a)\\1"] {
        assert!(
            Rule::regex("bad", pattern, Action::Block)
                .validate()
                .is_err(),
            "{pattern}"
        );
    }
    assert!(
        config(vec![Rule::regex("test", "x".repeat(4097), Action::Block)])
            .validate()
            .is_err()
    );
}

#[test]
fn fragments_validate_and_union_overlapping_unicode_spans() {
    let text = "hello αβγδ goodbye αβγδ";
    let spans = fragments(text, &["αβγ".into(), "βγδ".into()]).unwrap();
    assert_eq!(spans.len(), 2);
    assert_eq!(
        detectors::redact(text, &spans, "[redacted]", 0, false),
        "hello [redacted] goodbye [redacted]"
    );
    assert!(fragments(text, &["not in source".into()]).is_err());
    assert!(fragments(text, &[String::new()]).is_err());
}

#[tokio::test]
async fn cards_use_luhn_and_mask_only_valid_candidates() {
    let c = config(vec![Rule::credit_card(Action::Redact {
        replacement: "[card]".into(),
        keep_last: 4,
    })]);
    let e = evaluate(
        &c,
        &Registry::default(),
        "Pay 4111 1111 1111 1111, invalid 4111 1111 1111 1112.",
    )
    .await;
    assert_eq!(e.text, "Pay [card] 1111, invalid 4111 1111 1111 1112.");
    assert!(e.block.is_none() && !e.unavailable);
    assert!(!serde_json::to_string(&e.findings).unwrap().contains("4111"));
}

#[tokio::test]
async fn secrets_and_custom_patterns_are_removed() {
    let c = config(vec![
        Rule::secrets(Action::redact()),
        Rule::regex("company", "ACME_[A-Za-z0-9]{8}", Action::redact()),
    ]);
    let e = evaluate(
        &c,
        &Registry::default(),
        "α sk-abcdefghijklmnopqrstuvwxyz ACME_12345678 ω",
    )
    .await;
    assert_eq!(e.text, "α [redacted] [redacted] ω");
    assert!(!e.unavailable && e.block.is_none());
    let e = evaluate(
        &config(vec![Rule::regex("policy", "secret", Action::Block)]),
        &Registry::default(),
        "a secret",
    )
    .await;
    assert!(matches!(e.block, Some(Decision::Block { .. })));
}

#[tokio::test]
async fn replacement_callbacks_receive_matches_once_and_recheck_final_text() {
    let n = Arc::new(AtomicUsize::new(0));
    let count = n.clone();
    let registry = registered(move |ctx| {
        count.fetch_add(1, Ordering::SeqCst);
        assert_eq!(ctx.matches[0].text, "secret");
        Ok(Decision::Replace {
            text: ctx.text.replace("secret", "[hidden]"),
        })
    });
    let c = config(vec![Rule::regex("custom", "secret", callback())]);
    let e = evaluate(&c, &registry, "a secret").await;
    assert_eq!(e.text, "a [hidden]");
    assert_eq!(n.load(Ordering::SeqCst), 1);
    assert!(e.block.is_none());
    let registry = registered(|_| {
        Ok(Decision::Replace {
            text: "secret".into(),
        })
    });
    let c = config(vec![
        Rule::regex("first", "secret", Action::redact()),
        Rule::regex("second", "trigger", callback()),
    ]);
    let e = evaluate(&c, &registry, "secret trigger").await;
    assert!(matches!(e.block,Some(Decision::Block{code,..})if code=="SANITIZATION_INCOMPLETE"));
}

struct Slow;
impl Handler for Slow {
    fn call(&self, _: Context, _: CancellationToken) -> CheckFuture<'_> {
        Box::pin(std::future::pending())
    }
}
#[tokio::test]
async fn callback_errors_panics_missing_handlers_timeouts_and_cancellation_fail_closed() {
    let mut rule = Rule::regex("custom", "secret", callback());
    rule.timeout_ms = 1;
    let c = config(vec![rule]);
    let slow = Registry::default();
    slow.register(handler_ref(), Arc::new(Slow)).unwrap();
    for registry in [
        Registry::default(),
        registered(|_| anyhow::bail!("sensitive error")),
        registered(|_| panic!("handler panic")),
        registered(|_| Ok(Decision::Allow)),
        slow,
    ] {
        let e = evaluate(&c, &registry, "secret").await;
        assert!(e.unavailable);
        assert!(!serde_json::to_string(&e.findings)
            .unwrap()
            .contains("secret"));
    }
    let cancel = CancellationToken::new();
    cancel.cancel();
    let e = apply(
        &c,
        &Registry::default(),
        None,
        context("secret", Phase::UserInput),
        cancel,
    )
    .await
    .unwrap();
    assert!(e.unavailable);
}

struct FakeModel(Vec<String>);
impl ModelDetector for FakeModel {
    fn detect(&self, _: ModelRef, _: String, _: Context, _: CancellationToken) -> DetectFuture<'_> {
        Box::pin(async { Ok(self.0.clone()) })
    }
}
#[tokio::test]
async fn model_findings_must_be_exact_source_fragments() {
    let c = config(vec![Rule {
        name: "pii".into(),
        version: "1".into(),
        detector: Detector::Model {
            model: ModelRef::new("privacy", "fixture"),
            instructions: "Find SSNs".into(),
        },
        action: Action::Block,
        timeout_ms: 10,
    }]);
    for (fragments, unavailable) in [
        (vec!["one two three".into()], false),
        (vec!["hallucination".into()], true),
        (vec![], false),
    ] {
        let e = apply(
            &c,
            &Registry::default(),
            Some(&FakeModel(fragments)),
            context("one two three", Phase::UserInput),
            CancellationToken::new(),
        )
        .await
        .unwrap();
        assert_eq!(e.unavailable, unavailable);
        if !unavailable {
            assert_eq!(
                e.block.is_some(),
                !e.findings.iter().all(|f| f.match_count == 0)
            );
        }
    }
}

#[tokio::test]
async fn hooks_see_sanitized_content_cannot_rewrite_and_observers_cannot_block() {
    let registry = registered(|ctx| {
        assert_eq!(ctx.text, "[redacted]");
        Ok(Decision::block("HOOK_BLOCK"))
    });
    let mut c = Config {
        guardrails: Guardrails {
            model_output: vec![Rule::regex("pii", "secret", Action::redact())],
            ..Default::default()
        },
        hooks: vec![Hook {
            name: "output".into(),
            version: "1".into(),
            when: HookPhase::BeforeOutput,
            tools: vec![],
            handler: handler_ref(),
            timeout_ms: 10,
        }],
        ..Default::default()
    };
    let e = apply(
        &c,
        &registry,
        None,
        context("secret", Phase::ModelOutput),
        CancellationToken::new(),
    )
    .await
    .unwrap();
    assert!(matches!(e.block,Some(Decision::Block{code,..})if code=="HOOK_BLOCK"));
    let registry = registered(|_| {
        Ok(Decision::Replace {
            text: "unchecked".into(),
        })
    });
    let e = apply(
        &c,
        &registry,
        None,
        context("secret", Phase::ModelOutput),
        CancellationToken::new(),
    )
    .await
    .unwrap();
    assert!(e.unavailable);
    c.hooks[0].when = HookPhase::OnEvent;
    let e = apply(
        &c,
        &Registry::default(),
        None,
        context("secret", Phase::ModelOutput),
        CancellationToken::new(),
    )
    .await
    .unwrap();
    assert!(!e.unavailable && e.block.is_none());
    assert!(
        !observe(
            &c.hooks[0],
            &Registry::default(),
            context("safe", Phase::Event),
            CancellationToken::new()
        )
        .await
    );
}

#[test]
fn inherited_checks_cannot_be_replaced_or_retry_limits_increased() {
    let mut c = config(vec![Rule::regex("required", "secret", Action::Block)]);
    c.blocked_output_options = Some(BlockedOutputOptions { max_retries: 1 });
    assert!(c
        .merge(&config(vec![Rule::regex(
            "required",
            "different",
            Action::Block
        )]))
        .is_err());
    assert!(c
        .merge(&Config {
            blocked_output_options: Some(BlockedOutputOptions { max_retries: 2 }),
            ..Default::default()
        })
        .is_err());
    let merged = c
        .merge(&config(vec![Rule::credit_card(Action::redact())]))
        .unwrap();
    assert_eq!(merged.guardrails.user_input.len(), 2);
    assert_eq!(c.merge(&c).unwrap(), c);
}

#[tokio::test]
async fn tool_selectors_are_exact_and_resource_bounded() {
    let c = Config {
        guardrails: Guardrails {
            tool_results: vec![ToolRules {
                tools: vec!["coding.read_file".into()],
                rules: vec![Rule::regex("pii", "secret", Action::Block)],
            }],
            ..Default::default()
        },
        ..Default::default()
    };
    let mut ctx = context("secret", Phase::ToolResult);
    ctx.tool = Some("coding.read_file".into());
    assert!(apply(
        &c,
        &Registry::default(),
        None,
        ctx.clone(),
        CancellationToken::new()
    )
    .await
    .unwrap()
    .block
    .is_some());
    ctx.tool = Some("coding.search".into());
    assert!(apply(
        &c,
        &Registry::default(),
        None,
        ctx,
        CancellationToken::new()
    )
    .await
    .unwrap()
    .block
    .is_none());
    assert!(
        evaluate(
            &config(vec![Rule::regex("limit", "a", Action::Block)]),
            &Registry::default(),
            &"a".repeat(MAX_MATCHES + 1)
        )
        .await
        .unavailable
    );
}

#[tokio::test]
async fn unchanged_callback_replacement_cannot_release_a_detected_secret() {
    let registry = registered(|ctx| Ok(Decision::Replace { text: ctx.text }));
    let e = evaluate(
        &config(vec![Rule::regex("custom", "secret", callback())]),
        &registry,
        "secret",
    )
    .await;
    assert!(matches!(e.block,Some(Decision::Block{code,..})if code=="SANITIZATION_INCOMPLETE"));
}

#[tokio::test]
async fn callback_block_feedback_cannot_echo_matched_content_or_reason_code() {
    let registry = registered(|c| {
        Ok(Decision::Block {
            code: c.text.clone(),
            feedback_to_model: format!("Blocked because {}", c.text),
        })
    });
    let config = config(vec![Rule::regex(
        "secret",
        "SECRET_[A-Z]+",
        Action::Callback {
            handler: handler_ref(),
        },
    )]);
    let result = evaluate(&config, &registry, "SECRET_SENTINEL").await;
    let decision = serde_json::to_string(&result.block).unwrap();
    assert!(!decision.contains("SECRET_SENTINEL"));
    assert!(!serde_json::to_string(&result.findings)
        .unwrap()
        .contains("SECRET_SENTINEL"));
}
