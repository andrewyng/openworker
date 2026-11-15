#![cfg(feature = "otlp")]
use axum::{
    body::Bytes,
    extract::{OriginalUri, State},
    routing::post,
    Router,
};
use opentelemetry_proto::tonic::{
    collector::{
        logs::v1::ExportLogsServiceRequest, metrics::v1::ExportMetricsServiceRequest,
        trace::v1::ExportTraceServiceRequest,
    },
    common::v1::{any_value::Value as V, KeyValue},
    trace::v1::Span,
};
use openworker_telemetry::{attributes as a, *};
use prost::Message;
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
type Packets = Arc<Mutex<Vec<(String, Bytes)>>>;
async fn collect(
    State(rows): State<Packets>,
    OriginalUri(uri): OriginalUri,
    body: Bytes,
) -> &'static str {
    rows.lock().unwrap().push((uri.path().into(), body));
    ""
}
fn attr<'a>(attrs: &'a [KeyValue], key: &str) -> Option<&'a V> {
    attrs
        .iter()
        .find(|a| a.key == key)
        .and_then(|a| a.value.as_ref())
        .and_then(|v| v.value.as_ref())
}
fn string(attrs: &[KeyValue], key: &str) -> String {
    match attr(attrs, key) {
        Some(V::StringValue(v)) => v.clone(),
        x => panic!("Missing string {key}: {x:?}"),
    }
}
fn registry(attrs: &[KeyValue], metric: bool) {
    let mut seen = std::collections::BTreeSet::new();
    for kv in attrs {
        assert!(seen.insert(&kv.key), "duplicate {}", kv.key);
        let f = a::field(&kv.key).unwrap_or_else(|| panic!("Unregistered {}", kv.key));
        if metric {
            assert!(f.metric_dimension, "Illegal dimension {}", kv.key);
        }
        assert!(matches!(
            (f.kind, kv.value.as_ref().and_then(|v| v.value.as_ref())),
            (a::Kind::String, Some(V::StringValue(_)))
                | (a::Kind::Int, Some(V::IntValue(_)))
                | (a::Kind::Bool, Some(V::BoolValue(_)))
                | (a::Kind::Double, Some(V::DoubleValue(_)))
        ));
    }
}
#[test]
fn shared_registry_has_unique_typed_privacy_and_dimension_contracts() {
    let value: serde_json::Value =
        serde_json::from_str(include_str!("../attributes.json")).unwrap();
    assert_eq!(value["version"], a::VERSION);
    assert_eq!(value["semconv_version"], a::SEMCONV);
    let rows = value["attributes"].as_array().unwrap();
    assert_eq!(rows.len(), a::FIELDS.len());
    let names: std::collections::BTreeSet<_> =
        rows.iter().map(|r| r["key"].as_str().unwrap()).collect();
    assert_eq!(names.len(), rows.len());
    for row in rows {
        let f = a::field(row["key"].as_str().unwrap()).unwrap();
        assert_eq!(row["metric_dimension"], f.metric_dimension);
    }
    assert_eq!(
        a::CACHE_WRITE_TOKENS,
        "gen_ai.usage.cache_creation.input_tokens"
    );
    assert!(a::field("gen_ai.usage.cache_write.input_tokens").is_none());
    assert!(metadata_attribute(a::MODEL_NAME, "provider/model-v1"));
    for value in [
        "https://user:password@host",
        "/Users/private/file",
        "sk-thisisafakelongcredential0123456",
        "name\ncontrol",
    ] {
        assert!(!metadata_attribute(a::MODEL_NAME, value));
    }
    assert!(!metadata_attribute(a::INPUT_TOKENS, "100"));
    assert!(!export_attribute("private.reasoning", "SECRET"));
}
#[tokio::test(flavor = "multi_thread")]
async fn content_off_generations_have_usage_identity_and_links_without_names_or_parent_rollups() {
    let packets = Packets::default();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let sink = packets.clone();
    let server = tokio::spawn(async move {
        axum::serve(
            listener,
            Router::new()
                .route("/*path", post(collect))
                .with_state(sink),
        )
        .await
        .unwrap()
    });
    let (t, c) = tokio::task::spawn_blocking(move || {
        otlp::Config {
            endpoint,
            ..Default::default()
        }
        .start("worker")
        .unwrap()
    })
    .await
    .unwrap();
    let mut root = t.span(Operation::Turn, Some("PRIVATE_SESSION"), None);
    root.turn("PRIVATE_TURN");
    let first = root.context();
    root.usage(99999, 99999);
    root.finish(Outcome::Waiting);
    let mut resumed = t.span(Operation::Turn, Some("PRIVATE_SESSION"), None);
    resumed.turn("PRIVATE_TURN");
    resumed.link(first);
    let second = resumed.context();
    scope_turn("PRIVATE_TURN", second, async {
        for (purpose, usage, outcome) in [
            (
                ModelPurpose::Main,
                Some(TokenUsage {
                    input: Some(100),
                    output: Some(20),
                    cache_read: Some(40),
                    cache_write: Some(10),
                    reasoning: Some(5),
                }),
                Outcome::Error,
            ),
            (
                ModelPurpose::AutoApprover,
                Some(TokenUsage {
                    input: Some(0),
                    output: Some(0),
                    ..Default::default()
                }),
                Outcome::Ok,
            ),
            (ModelPurpose::Compaction, None, Outcome::Ok),
        ] {
            let mut span = t.span(Operation::Model, Some("PRIVATE_SESSION"), None);
            span.purpose(purpose);
            span.metadata(a::MODEL_NAME, "PRIVATE_MODEL");
            span.content(a::PROMPT, || panic!("Content-off must remain lazy"));
            span.model_started();
            if let Some(u) = usage {
                span.reported_usage(u);
            }
            span.first_output();
            span.finish(outcome);
        }
        let mut tool = t.span(Operation::Tool, Some("PRIVATE_SESSION"), None);
        tool.usage(88888, 88888);
        tool.metadata(a::TOOL_NAME, "PRIVATE_TOOL");
        tool.output_bytes(512);
        tool.finish(Outcome::Unknown);
    })
    .await;
    resumed.finish(Outcome::Ok);
    t.interval(
        Operation::QuestionWait,
        Outcome::Ok,
        "PRIVATE_SESSION",
        "PRIVATE_TURN",
        "PRIVATE_ASK",
        2000,
        1000,
    );
    assert!(
        tokio::task::spawn_blocking(move || c.shutdown(Duration::from_secs(5)))
            .await
            .unwrap()
    );
    let packets = packets.lock().unwrap();
    let mut spans: Vec<Span> = vec![];
    let mut logs = vec![];
    let mut metrics = vec![];
    for (path, body) in packets.iter() {
        assert!(!String::from_utf8_lossy(body).contains("PRIVATE"));
        match path.as_str() {
            "/v1/traces" => {
                let r = ExportTraceServiceRequest::decode(body.clone()).unwrap();
                for rs in r.resource_spans {
                    let attrs = &rs.resource.unwrap().attributes;
                    registry(attrs, false);
                    assert_eq!(string(attrs, a::SEMCONV_VERSION), a::SEMCONV);
                    assert_eq!(string(attrs, a::SCHEMA_VERSION), a::VERSION);
                    for ss in rs.scope_spans {
                        spans.extend(ss.spans)
                    }
                }
            }
            "/v1/logs" => {
                for r in ExportLogsServiceRequest::decode(body.clone())
                    .unwrap()
                    .resource_logs
                {
                    for s in r.scope_logs {
                        logs.extend(s.log_records)
                    }
                }
            }
            "/v1/metrics" => {
                for r in ExportMetricsServiceRequest::decode(body.clone())
                    .unwrap()
                    .resource_metrics
                {
                    for s in r.scope_metrics {
                        metrics.extend(s.metrics)
                    }
                }
            }
            _ => panic!(),
        }
    }
    assert_eq!(spans.len(), 9); // two segments, three completions/starts, one tool; skewed wait withheld
    let generations: Vec<_> = spans
        .iter()
        .filter(|s| string(&s.attributes, a::OBSERVATION_TYPE) == "generation")
        .collect();
    assert_eq!(generations.len(), 3);
    for s in &spans {
        registry(&s.attributes, false);
        assert_eq!(
            string(&s.attributes, a::SESSION_ID),
            string(&s.attributes, a::SESSION_HASH)
        );
        assert_eq!(
            string(&s.attributes, a::CONVERSATION_ID),
            string(&s.attributes, a::SESSION_HASH)
        );
        assert!(attr(&s.attributes, a::MODEL_NAME).is_none());
        assert!(attr(&s.attributes, a::TOOL_NAME).is_none());
        if s.name != "model.attempt" {
            for key in [
                a::INPUT_TOKENS,
                a::OUTPUT_TOKENS,
                a::CACHE_READ_TOKENS,
                a::CACHE_WRITE_TOKENS,
                a::REASONING_TOKENS,
            ] {
                assert!(attr(&s.attributes, key).is_none());
            }
        }
    }
    let main = generations
        .iter()
        .find(|s| string(&s.attributes, a::PURPOSE) == "main")
        .unwrap();
    assert_eq!(main.kind, 3); // CLIENT
    for (key, n) in [
        (a::INPUT_TOKENS, 100),
        (a::OUTPUT_TOKENS, 20),
        (a::CACHE_READ_TOKENS, 40),
        (a::CACHE_WRITE_TOKENS, 10),
        (a::REASONING_TOKENS, 5),
    ] {
        assert_eq!(attr(&main.attributes, key), Some(&V::IntValue(n)));
    }
    let zero = generations
        .iter()
        .find(|s| string(&s.attributes, a::PURPOSE) == "auto_approver")
        .unwrap();
    assert_eq!(
        attr(&zero.attributes, a::INPUT_TOKENS),
        Some(&V::IntValue(0))
    );
    let unknown = generations
        .iter()
        .find(|s| string(&s.attributes, a::PURPOSE) == "compaction")
        .unwrap();
    assert!(attr(&unknown.attributes, a::INPUT_TOKENS).is_none());
    let resumed = spans.iter().find(|s| s.span_id == second.span).unwrap();
    assert_eq!(resumed.trace_id, first.trace);
    assert_eq!(resumed.links[0].span_id, first.span);
    assert_eq!(main.parent_span_id, second.span);
    let start = spans.iter().find(|s| s.name == "model.requested").unwrap();
    assert_eq!(string(&start.attributes, a::OBSERVATION_TYPE), "event");
    assert!(logs
        .iter()
        .any(|l| attr(&l.attributes, a::TIMING_KNOWN) == Some(&V::BoolValue(false))));
    for l in &logs {
        registry(&l.attributes, false);
        assert!(l
            .attributes
            .iter()
            .all(|a| !a.key.starts_with("gen_ai.usage")));
    }
    use opentelemetry_proto::tonic::metrics::v1::metric::Data;
    for m in metrics {
        match m.data {
            Some(Data::Sum(s)) => {
                for p in s.data_points {
                    registry(&p.attributes, true)
                }
            }
            Some(Data::Gauge(s)) => {
                for p in s.data_points {
                    registry(&p.attributes, true)
                }
            }
            Some(Data::Histogram(s)) => {
                for p in s.data_points {
                    registry(&p.attributes, true)
                }
            }
            _ => panic!("unexpected metric"),
        }
    }
    server.abort();
}
