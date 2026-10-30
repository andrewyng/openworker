//! Explicit OpenTelemetry component injection. Components remain app-owned;
//! this sink flushes but never shuts down an application's providers.
use crate::{Outcome, Record, Sink, Snapshot};
use opentelemetry::{
    logs::{LogRecord, Logger, LoggerProvider, Severity},
    metrics::{Counter, Gauge, Histogram, MeterProvider},
    trace::{SpanContext, SpanId, SpanKind, Status, TraceFlags, TraceId, TraceState},
    InstrumentationScope, KeyValue,
};
use opentelemetry_sdk::{
    logs::SdkLoggerProvider,
    metrics::SdkMeterProvider,
    trace::{SpanData, SpanProcessor},
};
use std::sync::Arc;
pub struct OtelSink {
    traces: Option<Arc<dyn SpanProcessor>>,
    logs: SdkLoggerProvider,
    metrics: SdkMeterProvider,
    operations: Counter<u64>,
    duration: Histogram<f64>,
    tokens: Counter<u64>,
    retries: Counter<u64>,
    first: Histogram<f64>,
    drops: Gauge<u64>,
    failures: Gauge<u64>,
    observations: Vec<Gauge<u64>>,
    error_at: Gauge<u64>,
    latest_errors: [u64; 2],
}
pub(crate) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
impl OtelSink {
    pub fn new(
        traces: Arc<dyn SpanProcessor>,
        logs: SdkLoggerProvider,
        metrics: SdkMeterProvider,
    ) -> Self {
        Self::with_parts(Some(traces), logs, metrics)
    }
    /// Same as [`Self::new`] with traces optional (e.g. a metrics-only backend).
    pub fn with_parts(
        traces: Option<Arc<dyn SpanProcessor>>,
        logs: SdkLoggerProvider,
        metrics: SdkMeterProvider,
    ) -> Self {
        let meter = metrics.meter("openworker.harness");
        Self {
            traces,
            logs,
            metrics,
            error_at: meter
                .u64_gauge("openworker.operation.last_error_timestamp_seconds")
                .build(),
            latest_errors: [0; 2],
            observations: crate::Gauge::ALL
                .iter()
                .map(|g| meter.u64_gauge(g.name()).build())
                .collect(),
            operations: meter.u64_counter("openworker.operation.count").build(),
            duration: meter
                .f64_histogram("openworker.operation.duration")
                .with_unit("s")
                .build(),
            tokens: meter.u64_counter("openworker.model.tokens").build(),
            retries: meter.u64_counter("openworker.model.retry.count").build(),
            first: meter
                .f64_histogram("openworker.model.first_output.duration")
                .with_unit("s")
                .build(),
            drops: meter.u64_gauge("openworker.telemetry.dropped").build(),
            failures: meter
                .u64_gauge("openworker.telemetry.export_failures")
                .build(),
        }
    }
}
impl Sink for OtelSink {
    fn observe(&mut self, samples: &[crate::Observation], stats: Snapshot) -> bool {
        for s in samples {
            self.observations[s.gauge as usize].record(s.value, &[]);
        }
        self.drops.record(stats.dropped, &[]);
        self.failures.record(stats.export_failures, &[]);
        self.metrics.force_flush().is_ok()
    }
    fn export(&mut self, records: &[Record], stats: Snapshot) -> bool {
        let logger = self.logs.logger("openworker.harness");
        for r in records {
            let labels = [
                KeyValue::new("openworker.operation", r.operation.name()),
                KeyValue::new("openworker.outcome", r.outcome.name()),
            ];
            self.operations.add(1, &labels);
            // A timestamp also catches the first error of a short-lived process,
            // for which Prometheus may never have seen a zero counter baseline.
            if r.outcome == Outcome::Error {
                let index = match r.operation {
                    crate::Operation::Model => Some(0),
                    crate::Operation::Tool => Some(1),
                    _ => None,
                };
                if let Some(i) = index {
                    self.latest_errors[i] = self.latest_errors[i].max(
                        r.ended
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_secs(),
                    );
                    self.error_at
                        .record(self.latest_errors[i], &[labels[0].clone()]);
                }
            }
            if !matches!(
                r.operation,
                crate::Operation::ApprovalRequested
                    | crate::Operation::ModelStarted
                    | crate::Operation::ApprovalResolved
                    | crate::Operation::Cancellation
                    | crate::Operation::UnknownOutcome
                    | crate::Operation::QuestionRequested
                    | crate::Operation::QuestionResolved
            ) && r.timing_known
            {
                self.duration.record(r.elapsed.as_secs_f64(), &labels);
            }
            if r.operation == crate::Operation::Model && r.attempt.is_some_and(|n| n > 1) {
                self.retries.add(1, &[]);
            }
            for (kind, value) in [("input", r.input_tokens), ("output", r.output_tokens)] {
                if let Some(n) = value {
                    let mut dimensions = vec![KeyValue::new("direction", kind)];
                    if let Some(p) = r.purpose {
                        dimensions.push(KeyValue::new("openworker.model.purpose", p.name()));
                    }
                    self.tokens.add(n, &dimensions);
                }
            }
            if let Some(d) = r.first_output {
                self.first.record(d.as_secs_f64(), &[]);
            }
            let mut attrs = labels.to_vec();
            if let Some(s) = r.identity.session {
                attrs.push(KeyValue::new("openworker.session.hash", hex(&s)));
            }
            if let Some(s) = r.identity.action {
                attrs.push(KeyValue::new("openworker.action.hash", hex(&s)));
            }
            if let Some(n) = r.attempt {
                attrs.push(KeyValue::new("openworker.attempt", i64::from(n)));
            }
            if let Some(t) = r.turn_hash {
                attrs.push(KeyValue::new("openworker.turn.hash", hex(&t)));
            }
            if let Some(p) = r.purpose {
                attrs.push(KeyValue::new("openworker.model.purpose", p.name()));
            }
            attrs.push(KeyValue::new("openworker.timing.known", r.timing_known));
            let trace_id = TraceId::from_bytes(r.context.trace);
            let span_id = SpanId::from_bytes(r.context.span);
            // Opt-in metadata/content goes on spans only, never on metric
            // labels or the lifecycle log stream.
            let mut span_attrs = attrs.clone();
            for (key, value) in [
                ("gen_ai.usage.input_tokens", r.input_tokens),
                ("gen_ai.usage.output_tokens", r.output_tokens),
                (
                    "gen_ai.usage.cache_read.input_tokens",
                    r.usage_details.cache_read,
                ),
                (
                    "gen_ai.usage.cache_write.input_tokens",
                    r.usage_details.cache_write,
                ),
                (
                    "gen_ai.usage.reasoning.output_tokens",
                    r.usage_details.reasoning,
                ),
                ("openworker.tool.returned_bytes", r.output_bytes),
            ] {
                if let Some(n) = value.and_then(|n| i64::try_from(n).ok()) {
                    span_attrs.push(KeyValue::new(key, n));
                }
            }
            if !r.attributes.is_empty() {
                if let Some(s) = r.identity.session {
                    // Trace grouping for LLM backends (still the hash, not the ID).
                    span_attrs.push(KeyValue::new("session.id", hex(&s)));
                }
                span_attrs.extend(
                    r.attributes
                        .iter()
                        .map(|(k, v)| KeyValue::new(*k, v.clone())),
                );
            }
            // Cross-worker wall clocks can disagree. Preserve the unknown wait in
            // recording/logs; do not export an invalid negative-duration span.
            if let Some(traces) = self.traces.as_ref().filter(|_| r.timing_known) {
                traces.on_end(SpanData {
                    span_context: SpanContext::new(
                        trace_id,
                        span_id,
                        TraceFlags::SAMPLED,
                        false,
                        TraceState::default(),
                    ),
                    parent_span_id: r
                        .parent
                        .map(|p| SpanId::from_bytes(p.span))
                        .unwrap_or(SpanId::INVALID),
                    parent_span_is_remote: false,
                    span_kind: SpanKind::Internal,
                    name: r.operation.name().into(),
                    start_time: r.started,
                    end_time: r.ended,
                    attributes: span_attrs,
                    dropped_attributes_count: 0,
                    events: Default::default(),
                    links: {
                        let mut links = opentelemetry_sdk::trace::SpanLinks::default();
                        for c in &r.links {
                            links.links.push(opentelemetry::trace::Link::new(
                                SpanContext::new(
                                    TraceId::from_bytes(c.trace),
                                    SpanId::from_bytes(c.span),
                                    TraceFlags::SAMPLED,
                                    false,
                                    TraceState::default(),
                                ),
                                vec![],
                                0,
                            ));
                        }
                        links
                    },
                    status: if r.outcome == Outcome::Error {
                        Status::error("operation_failed")
                    } else {
                        Status::Unset
                    },
                    instrumentation_scope: InstrumentationScope::builder("openworker.harness")
                        .with_version("0.1.0")
                        .build(),
                });
            }
            // Structured lifecycle diagnostics, never raw runtime logs or stderr.
            let mut log = logger.create_log_record();
            log.set_event_name(r.operation.name());
            log.set_timestamp(r.ended);
            log.set_severity_number(if r.outcome == Outcome::Error {
                Severity::Error
            } else {
                Severity::Info
            });
            log.set_trace_context(trace_id, span_id, Some(TraceFlags::SAMPLED));
            for a in attrs {
                match a.value {
                    opentelemetry::Value::String(s) => log.add_attribute(a.key, s.to_string()),
                    opentelemetry::Value::I64(n) => log.add_attribute(a.key, n),
                    _ => {}
                }
            }
            logger.emit(log);
        }
        self.drops.record(stats.dropped, &[]);
        self.failures.record(stats.export_failures, &[]);
        // Only this dedicated telemetry thread waits for exporters.
        let a = self.traces.as_ref().is_none_or(|t| t.force_flush().is_ok());
        let b = self.logs.force_flush().is_ok();
        let c = self.metrics.force_flush().is_ok();
        a && b && c
    }
}
