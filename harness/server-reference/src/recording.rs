//! Optional recording database, configured independently of execution storage.
use crate::Config;
use anyhow::{ensure, Context, Result};
use openworker_observability::{
    postgres::{Config as Database, Postgres, Tls},
    Recorder, RecordingSink,
};
use openworker_telemetry::{Capture, ContentPolicy, Controller, Sink, Telemetry};
use serde::Deserialize;
use std::{path::PathBuf, time::Duration};

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Options {
    #[serde(default = "schema")]
    pub schema: String,
    pub database: Option<Connection>,
    #[serde(default = "capacity")]
    pub queue_capacity: usize,
    #[serde(default = "retention")]
    pub retention_days: u16,
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Connection {
    pub database_env: String,
    pub database_ca: Option<PathBuf>,
    #[serde(default)]
    pub database_loopback_plaintext: bool,
    #[serde(default = "timeout")]
    pub timeout_ms: u64,
}
fn schema() -> String {
    "ow_observability".into()
}
fn capacity() -> usize {
    1024
}
fn retention() -> u16 {
    30
}
fn timeout() -> u64 {
    1000
}
impl Options {
    pub fn database(&self, execution: &Config) -> Result<Database> {
        ensure!(
            self.schema != execution.schema,
            "Observation schema must be separate from execution schema"
        );
        ensure!(
            (1..=2048).contains(&self.queue_capacity) && (1..=365).contains(&self.retention_days),
            "Invalid recording queue or retention settings"
        );
        let mut db = if let Some(c) = &self.database {
            ensure!(
                !c.database_env.is_empty()
                    && c.database_env.len() <= 100
                    && c.database_env
                        .bytes()
                        .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
                "Invalid observation database credential reference"
            );
            let mut d = Database::new(
                std::env::var(&c.database_env)
                    .context("Observation database credential variable unavailable")?,
                self.schema.clone(),
            );
            d.tls = if c.database_loopback_plaintext {
                Tls::LoopbackPlaintext
            } else {
                Tls::Verified {
                    ca_file: c.database_ca.clone(),
                }
            };
            d.timeout = Duration::from_millis(c.timeout_ms);
            d
        } else {
            // Copies configuration only. Postgres::open creates a fresh connection
            // exclusively owned by the recorder, never the state-store connection.
            let source = execution.database()?;
            let mut d = Database::new(source.connection.to_string(), self.schema.clone());
            d.tls = match source.tls {
                openworker_store_postgres::Tls::LoopbackPlaintext => Tls::LoopbackPlaintext,
                openworker_store_postgres::Tls::Verified { ca_file } => Tls::Verified { ca_file },
            };
            d
        };
        // Keep copied secrets in zeroizing configuration; never expose via Debug.
        db.schema = self.schema.clone();
        db.validate()?;
        Ok(db)
    }
}
pub struct Started {
    pub telemetry: Telemetry,
    pub telemetry_controller: Option<Controller>,
    pub recorder_controller: Option<openworker_observability::Controller>,
    pub statistics: Option<openworker_observability::Statistics>,
}
/// Call from a blocking thread. No migration or execution-store connection here.
pub fn start(config: &Config, role: &'static str, enable_recording: bool) -> Result<Started> {
    let Some(options) = config.observability.as_ref().filter(|_| enable_recording) else {
        let (telemetry, controller) = match &config.telemetry {
            Some(c) => {
                let (t, c) = c.start(role)?;
                (t, Some(c))
            }
            None => (Telemetry::default(), None),
        };
        return Ok(Started {
            telemetry,
            telemetry_controller: controller,
            recorder_controller: None,
            statistics: None,
        });
    };
    let database = options.database(config)?;
    let store = Postgres::open(&database)?;
    let exporter: Option<Box<dyn Sink>> = config
        .telemetry
        .as_ref()
        .map(|c| c.build_sink(role).map(|s| Box::new(s) as Box<dyn Sink>))
        .transpose()?;
    let export_content = config
        .telemetry
        .as_ref()
        .map(|c| c.content.clone())
        .unwrap_or_default();
    let (recorder, controller) = Recorder::start(
        Box::new(store),
        config.schema.clone(),
        options.queue_capacity,
        options.retention_days,
    )?;
    let statistics = recorder.statistics();
    let sink = RecordingSink::new(
        recorder,
        config.schema.clone(),
        role.into(),
        exporter,
        export_content.capture,
    )?;
    // At least identifying metadata locally; independent export filter in the sink.
    let content = ContentPolicy {
        capture: export_content.capture.max(Capture::Metadata),
        ..export_content
    };
    let (telemetry, telemetry_controller) = Telemetry::start_with_content(sink, 2048, content)?;
    Ok(Started {
        telemetry,
        telemetry_controller: Some(telemetry_controller),
        recorder_controller: Some(controller),
        statistics: Some(statistics),
    })
}
