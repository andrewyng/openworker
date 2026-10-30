#![cfg(feature = "otlp")]
use axum::{
    body::Bytes,
    extract::{OriginalUri, State},
    routing::post,
    Router,
};
use openworker_telemetry::*;
use prost::Message;
use std::{
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
type Packets = Arc<Mutex<Vec<(String, Bytes)>>>;

#[tokio::test(flavor = "multi_thread")]
async fn slow_collector_cannot_extend_shutdown_budget() {
    async fn slow() -> &'static str {
        tokio::time::sleep(Duration::from_secs(15)).await;
        ""
    }
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(listener, Router::new().route("/*path", post(slow)))
            .await
            .unwrap()
    });
    let (t, c) = tokio::task::spawn_blocking(move || {
        otlp::Config {
            endpoint,
            deployment: "test".into(),
            ..Default::default()
        }
        .start("test")
        .unwrap()
    })
    .await
    .unwrap();
    t.event(Operation::Tool, Outcome::Ok, None, None);
    tokio::time::sleep(Duration::from_millis(100)).await;
    let now = Instant::now();
    let flushed = tokio::task::spawn_blocking(move || c.shutdown(Duration::from_millis(100)))
        .await
        .unwrap();
    assert!(!flushed);
    assert!(now.elapsed() < Duration::from_secs(1));
    let now = Instant::now();
    for _ in 0..1000 {
        t.event(Operation::Model, Outcome::Ok, None, None);
    }
    assert!(now.elapsed() < Duration::from_secs(1));
    assert!(t.snapshot().dropped >= 1000);
    server.abort();
}
async fn receive(
    State(p): State<Packets>,
    OriginalUri(uri): OriginalUri,
    bytes: Bytes,
) -> &'static str {
    p.lock().unwrap().push((uri.path().into(), bytes));
    ""
}
#[tokio::test(flavor = "multi_thread")]
async fn all_signals_have_valid_otlp_and_no_payload_or_metric_identifiers() {
    let packets = Packets::default();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/v1/traces", post(receive))
        .route("/v1/metrics", post(receive))
        .route("/v1/logs", post(receive))
        .with_state(packets.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let (t, c) = tokio::task::spawn_blocking(move || {
        otlp::Config {
            endpoint,
            deployment: "test".into(),
            ..Default::default()
        }
        .start("test")
        .unwrap()
    })
    .await
    .unwrap();
    let mut root = t.span(
        Operation::Turn,
        Some("SECRET_SESSION"),
        Some("PROMPT_SECRET"),
    );
    root.turn("SECRET_TURN");
    let cx = root.context();
    scope_turn("SECRET_TURN", cx, async {
        let mut child = t.span(Operation::Model, Some("SECRET_SESSION"), None);
        child.reported_usage(TokenUsage {
            input: Some(42),
            output: Some(9),
            cache_read: Some(12),
            cache_write: Some(5),
            reasoning: Some(3),
        });
        child.purpose(ModelPurpose::Compaction);
        child.link(TraceContext {
            trace: [1; 16],
            span: [2; 8],
        });
        child.first_output();
        child.finish(Outcome::Error)
    })
    .await;
    root.finish(Outcome::Waiting);
    t.observe(Gauge::TurnsQueued, 7);
    t.observe(Gauge::StateObservedAt, 1234567890);
    assert!(
        tokio::task::spawn_blocking(move || c.shutdown(Duration::from_secs(5)))
            .await
            .unwrap()
    );
    let packets = packets.lock().unwrap();
    let mut trace_rows = vec![];
    let mut logs = 0;
    let mut metrics = vec![];
    for (path, data) in packets.iter() {
        assert!(!String::from_utf8_lossy(data).contains("SECRET"));
        match path.as_str() {
            "/v1/traces" => {
                let p=opentelemetry_proto::tonic::collector::trace::v1::ExportTraceServiceRequest::decode(data.clone()).unwrap();
                for r in p.resource_spans {
                    for s in r.scope_spans {
                        trace_rows.extend(s.spans)
                    }
                }
            }
            "/v1/logs" => {
                let p=opentelemetry_proto::tonic::collector::logs::v1::ExportLogsServiceRequest::decode(data.clone()).unwrap();
                for r in p.resource_logs {
                    for s in r.scope_logs {
                        logs += s.log_records.len()
                    }
                }
            }
            "/v1/metrics" => {
                let p=opentelemetry_proto::tonic::collector::metrics::v1::ExportMetricsServiceRequest::decode(data.clone()).unwrap();
                for r in p.resource_metrics {
                    assert!(format!("{:?}", r.resource).contains("openworker.deployment"));
                    for s in r.scope_metrics {
                        metrics.extend(s.metrics)
                    }
                }
            }
            _ => panic!(),
        }
    }
    assert_eq!(trace_rows.len(), 2);
    assert_eq!(logs, 2);
    let parent = trace_rows
        .iter()
        .find(|s| s.name == "agent.execute")
        .unwrap();
    let child = trace_rows
        .iter()
        .find(|s| s.name == "model.attempt")
        .unwrap();
    assert_eq!(child.parent_span_id, parent.span_id);
    assert_eq!(child.trace_id, parent.trace_id);
    assert_eq!(child.links.len(), 1);
    assert_eq!(child.links[0].span_id, vec![2; 8]);
    for (key, value) in [
        ("gen_ai.usage.input_tokens", 42),
        ("gen_ai.usage.cache_read.input_tokens", 12),
        ("gen_ai.usage.cache_creation.input_tokens", 5),
        ("gen_ai.usage.reasoning.output_tokens", 3),
    ] {
        assert!(child.attributes.iter().any(|a|a.key==key&&matches!(a.value.as_ref().and_then(|v|v.value.as_ref()),Some(opentelemetry_proto::tonic::common::v1::any_value::Value::IntValue(n)) if *n==value)));
    }
    assert!(child
        .attributes
        .iter()
        .any(|a| a.key == "openworker.turn.hash"));
    let m = format!("{metrics:?}");
    assert!(m.contains("openworker.model.tokens"));
    assert!(m.contains("openworker.operation.last_error_timestamp_seconds"));
    assert!(!m.contains("session.hash"));
    assert!(!m.contains("action.hash"));
    assert!(!m.contains("turn.hash"));
    let queued = metrics
        .iter()
        .find(|m| m.name == Gauge::TurnsQueued.name())
        .unwrap();
    let Some(opentelemetry_proto::tonic::metrics::v1::metric::Data::Gauge(g)) = &queued.data else {
        panic!("Expected gauge")
    };
    assert!(g.data_points.iter().all(|p| p.attributes.is_empty()));
    assert_eq!(
        g.data_points[0].value,
        Some(opentelemetry_proto::tonic::metrics::v1::number_data_point::Value::AsInt(7))
    );
    server.abort();
}
#[test]
fn deployment_configuration_is_backward_compatible_and_bounded() {
    let c: otlp::Config = serde_json::from_str(r#"{"endpoint":"http://127.0.0.1:4318"}"#).unwrap();
    assert_eq!(c.deployment, "default");
    for deployment in [
        "".into(),
        "x".repeat(65),
        "user@example.org".into(),
        "bad\nlabel".into(),
    ] {
        assert!(otlp::Config {
            endpoint: c.endpoint.clone(),
            deployment,
            ..Default::default()
        }
        .start("test")
        .is_err());
    }
}
#[tokio::test(flavor = "multi_thread")]
async fn collector_outage_and_bad_endpoints() {
    for endpoint in [
        "http://example.org",
        "https://user:password@example.org",
        "https://example.org?secret=x",
        "https://example.org/v1/traces",
    ] {
        let endpoint = endpoint.to_owned();
        assert!(tokio::task::spawn_blocking(move || otlp::Config {
            endpoint,
            deployment: "test".into(),
            ..Default::default()
        }
        .start("test"))
        .await
        .unwrap()
        .is_err());
    }
    let socket = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}", socket.local_addr().unwrap());
    drop(socket);
    let (t, c) = tokio::task::spawn_blocking(move || {
        otlp::Config {
            endpoint,
            deployment: "test".into(),
            ..Default::default()
        }
        .start("test")
        .unwrap()
    })
    .await
    .unwrap();
    let now = Instant::now();
    for _ in 0..10000 {
        t.event(Operation::Tool, Outcome::Ok, None, None)
    }
    assert!(now.elapsed() < Duration::from_secs(2));
    let until = Instant::now() + Duration::from_secs(5);
    while c.snapshot().export_failures == 0 && Instant::now() < until {
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    assert!(c.snapshot().export_failures > 0);
    let now = Instant::now();
    tokio::task::spawn_blocking(move || c.shutdown(Duration::from_secs(5)))
        .await
        .unwrap();
    assert!(now.elapsed() < Duration::from_secs(6));
}

type Seen = Arc<Mutex<Vec<(String, axum::http::HeaderMap, Bytes)>>>;
async fn receive_with_headers(
    State(p): State<Seen>,
    OriginalUri(uri): OriginalUri,
    headers: axum::http::HeaderMap,
    bytes: Bytes,
) -> &'static str {
    p.lock().unwrap().push((uri.path().into(), headers, bytes));
    ""
}

#[tokio::test(flavor = "multi_thread")]
async fn authenticated_base_path_traces_only_with_opt_in_content() {
    let seen = Seen::default();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/api/public/otel", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/api/public/otel/v1/traces", post(receive_with_headers))
        .route("/api/public/otel/v1/logs", post(receive_with_headers))
        .route("/api/public/otel/v1/metrics", post(receive_with_headers))
        .with_state(seen.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    std::env::set_var("OW_TEST_OTLP_AUTH", "Basic dGVzdDpzZWNyZXQ=");
    let config = otlp::Config {
        endpoint,
        deployment: "test".into(),
        headers: vec![
            otlp::Header::secret("Authorization", "OW_TEST_OTLP_AUTH"),
            otlp::Header::literal("x-langfuse-ingestion-version", "4"),
        ],
        signals: vec![otlp::Signal::Traces],
        acknowledgement: otlp::Acknowledgement::Otlp,
        content: ContentPolicy {
            capture: Capture::Full,
            max_bytes: 64,
            redact_secrets: true,
        },
    };
    assert!(
        !format!("{config:?}").contains("dGVzdDpzZWNyZXQ="),
        "config never holds the secret"
    );
    let (t, c) = tokio::task::spawn_blocking(move || config.start("test").unwrap())
        .await
        .unwrap();
    let mut span = t.span(Operation::Model, Some("session-1"), None);
    assert_eq!(span.capture(), Capture::Full);
    span.metadata("gen_ai.request.model", "gpt-test");
    span.content_tail("gen_ai.prompt", || {
        format!("{} use sk-abcdefghijklmnopqrstuvwxyz now", "x".repeat(200))
    });
    span.content("gen_ai.completion", || "done".into());
    span.finish(Outcome::Ok);
    assert!(
        tokio::task::spawn_blocking(move || c.shutdown(Duration::from_secs(5)))
            .await
            .unwrap()
    );
    server.abort();
    let seen = seen.lock().unwrap();
    assert!(!seen.is_empty());
    for (path, headers, body) in seen.iter() {
        assert_eq!(path, "/api/public/otel/v1/traces", "only selected signals");
        assert_eq!(headers["authorization"], "Basic dGVzdDpzZWNyZXQ=");
        assert_eq!(headers["accept"], "application/x-protobuf");
        assert_eq!(headers["x-langfuse-ingestion-version"], "4");
        let text = String::from_utf8_lossy(body);
        assert!(
            !text.contains("abcdefghijklmnopqrstuvwxyz"),
            "secret redacted"
        );
        assert!(text.contains("gen_ai.prompt") && text.contains("sk-[REDACTED] now"));
        assert!(text.contains("gpt-test") && text.contains("session.id"));
        assert!(!text.contains(&"x".repeat(100)), "kept only the tail");
    }
}

#[test]
fn content_off_by_default_and_header_rules_enforced() {
    let base = || otlp::Config {
        endpoint: "https://otlp.example.com".into(),
        ..Default::default()
    };
    assert_eq!(base().content.capture, Capture::Off);
    let cases = [
        vec![otlp::Header::secret(
            "Authorization",
            "OW_TEST_DEFINITELY_UNSET",
        )],
        vec![otlp::Header::literal("content-type", "text/plain")],
        vec![otlp::Header::literal("Authorization", "Basic fixture-only")],
        vec![otlp::Header::literal("proxy-authorization", "x")],
        vec![otlp::Header::secret("Authorization", "lower_case")],
        vec![otlp::Header {
            name: "x-both".into(),
            value: Some("a".into()),
            value_env: Some("B".into()),
        }],
        vec![otlp::Header::literal("bad header", "x")],
        vec![
            otlp::Header::literal("x-a", "1"),
            otlp::Header::literal("X-A", "2"),
        ],
    ];
    for headers in cases {
        let config = otlp::Config {
            headers: headers.clone(),
            ..base()
        };
        assert!(config.start("test").is_err(), "{headers:?}");
    }
    // Headers never travel over plaintext to a remote host.
    let plaintext = otlp::Config {
        endpoint: "http://otlp.example.com".into(),
        headers: vec![otlp::Header::literal("x-a", "1")],
        ..Default::default()
    };
    assert!(plaintext.start("test").is_err());
    let no_signals = otlp::Config {
        signals: vec![],
        ..base()
    };
    assert!(no_signals.start("test").is_err());
}

#[test]
fn redaction_masks_common_credentials() {
    let r = redact("key=sk-proj-abcdefghijklmnop1234 and ghp_ABCDEFGHIJKLMNOPQRSTUV ok"); // gitleaks:allow -- deliberately invalid synthetic redaction fixtures
    assert!(!r.contains("abcdefghijklmnop1234") && !r.contains("ABCDEFGHIJKLMNOPQRSTUV"));
    assert!(r.ends_with(" ok"));
    let r = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig done");
    assert_eq!(r, "Authorization: Bearer [REDACTED] done");
    let r = redact("a\n-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\nb");
    assert_eq!(r, "a\n[REDACTED PRIVATE KEY]\nb");
    assert_eq!(redact("ask-me about sk-short"), "ask-me about sk-short");
    assert_eq!(redact("naïve café"), "naïve café");
}

#[tokio::test(flavor = "multi_thread")]
async fn rejection_redirect_partial_malformed_and_large_acknowledgements_are_not_success_or_retried(
) {
    use axum::{
        body::Body,
        http::{Response, StatusCode},
    };
    use opentelemetry_proto::tonic::collector::trace::v1::{
        ExportTracePartialSuccess, ExportTraceServiceResponse,
    };
    use std::sync::atomic::{AtomicUsize, Ordering};
    for mode in [
        "unauthorized",
        "redirect",
        "partial",
        "warning",
        "malformed",
        "oversized",
        "json_ok",
        "json_partial",
        "json_malformed",
        "json_wrong_signal",
        "job_standard",
        "job_vendor",
        "job_invalid",
    ] {
        let calls = Arc::new(AtomicUsize::new(0));
        let seen = calls.clone();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let app = Router::new().route(
            "/v1/traces",
            post(move || {
                let seen = seen.clone();
                async move {
                    seen.fetch_add(1, Ordering::SeqCst);
                    let (status, body) = match mode {
                        "unauthorized" => {
                            (StatusCode::UNAUTHORIZED, b"PRIVATE_COLLECTOR_ECHO".to_vec())
                        }
                        "redirect" => (StatusCode::TEMPORARY_REDIRECT, vec![]),
                        "partial" | "warning" => (
                            StatusCode::OK,
                            ExportTraceServiceResponse {
                                partial_success: Some(ExportTracePartialSuccess {
                                    rejected_spans: if mode == "partial" { 1 } else { 0 },
                                    error_message: "PRIVATE_COLLECTOR_ECHO".into(),
                                }),
                            }
                            .encode_to_vec(),
                        ),
                        "job_standard" | "job_vendor" | "job_invalid" => (StatusCode::OK, serde_json::to_vec(&serde_json::json!({"name":"otel-ingestion-job","id":"fixture","data":{"name":"otel-ingestion-job","payload":{"ingestionVersion":"4","authCheck":{"validKey":mode != "job_invalid"}}}})).unwrap()),
                        "json_ok" => (StatusCode::OK, b"{}".to_vec()),
                        "json_partial" => (StatusCode::OK, br#"{"partialSuccess":{"rejectedSpans":"1"}}"#.to_vec()),
                        "json_malformed" => (StatusCode::OK, b"not json".to_vec()),
                        "json_wrong_signal" => (StatusCode::OK, br#"{"partialSuccess":{"rejectedDataPoints":0}}"#.to_vec()),
                        "malformed" => (StatusCode::OK, vec![0xff]),
                        "oversized" => (StatusCode::OK, vec![0; 65537]),
                        _ => unreachable!(),
                    };
                    Response::builder()
                        .status(status)
                        .header("location", "http://127.0.0.1:1/not-followed")
                        .header("content-type", if mode.starts_with("json_") || mode.starts_with("job_") { "application/json; charset=utf-8" } else { "application/x-protobuf" })
                        .body(Body::from(body))
                        .unwrap()
                }
            }),
        );
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let (t, c) = tokio::task::spawn_blocking(move || {
            otlp::Config {
                endpoint,
                signals: vec![otlp::Signal::Traces],
                acknowledgement: if mode == "job_vendor" || mode == "job_invalid" {
                    otlp::Acknowledgement::LangfuseV4
                } else {
                    otlp::Acknowledgement::Otlp
                },
                ..Default::default()
            }
            .start("test")
            .unwrap()
        })
        .await
        .unwrap();
        t.event(
            Operation::Tool,
            Outcome::Unknown,
            Some("private-session"),
            None,
        );
        let c = tokio::task::spawn_blocking(move || {
            assert!(c.shutdown(Duration::from_secs(5)));
            c
        })
        .await
        .unwrap();
        assert_eq!(
            calls.load(Ordering::SeqCst),
            1,
            "{mode}: never replay a rejected/unknown export batch"
        );
        assert!(
            (c.snapshot().export_failures == 0) == (mode == "json_ok" || mode == "job_vendor"),
            "{mode}: report only complete acceptance: {:?}",
            c.snapshot()
        );
        server.abort();
    }
}
#[tokio::test(flavor = "multi_thread")]
async fn ambient_headers_resources_and_proxy_are_not_inherited() {
    struct Reset(Vec<(&'static str, Option<String>)>);
    impl Drop for Reset {
        fn drop(&mut self) {
            for (k, v) in &self.0 {
                if let Some(v) = v {
                    std::env::set_var(k, v)
                } else {
                    std::env::remove_var(k)
                }
            }
        }
    }
    let names = [
        (
            "OTEL_EXPORTER_OTLP_HEADERS",
            "x-ambient=PRIVATE_AMBIENT_SECRET",
        ),
        (
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
            "x-signal=PRIVATE_SIGNAL_SECRET",
        ),
        ("OTEL_RESOURCE_ATTRIBUTES", "ambient.key=PRIVATE_RESOURCE"),
        ("HTTP_PROXY", "http://127.0.0.1:1"),
        ("HTTPS_PROXY", "http://127.0.0.1:1"),
    ];
    let _reset = Reset(
        names
            .iter()
            .map(|(k, v)| {
                let old = std::env::var(k).ok();
                std::env::set_var(k, v);
                (*k, old)
            })
            .collect(),
    );
    let seen = Seen::default();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/v1/traces", post(receive_with_headers))
        .with_state(seen.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let (t, c) = tokio::task::spawn_blocking(move || {
        otlp::Config {
            endpoint,
            signals: vec![otlp::Signal::Traces],
            ..Default::default()
        }
        .start("test")
        .unwrap()
    })
    .await
    .unwrap();
    t.event(Operation::Model, Outcome::Ok, None, None);
    assert!(
        tokio::task::spawn_blocking(move || c.shutdown(Duration::from_secs(5)))
            .await
            .unwrap()
    );
    let rows = seen.lock().unwrap();
    assert_eq!(rows.len(), 1);
    for (_, headers, body) in rows.iter() {
        assert!(headers.get("x-ambient").is_none() && headers.get("x-signal").is_none());
        assert!(!String::from_utf8_lossy(body).contains("PRIVATE"));
    }
    server.abort();
}
