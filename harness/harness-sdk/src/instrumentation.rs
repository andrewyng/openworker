//! SDK composition of the same recorder/exporter used by the service.
use crate::HostConfig;
use anyhow::Result;
use openworker_telemetry::{Controller, Telemetry};

pub(crate) struct Started {
    #[cfg(feature = "evaluation-export")]
    pub evaluation_exporter: Option<std::sync::Arc<crate::evaluation_export::Controller>>,
    pub telemetry: Telemetry,
    pub controller: Option<Controller>,
    #[cfg(feature = "recording")]
    pub recording: Option<std::sync::Arc<Recording>>,
}
pub(crate) fn start(config: &HostConfig) -> Result<Started> {
    #[cfg(feature = "recording")]
    if let Some(options) = &config.recording {
        use openworker_observability::{Recorder, RecordingSink};
        use openworker_telemetry::{Capture, ContentPolicy, Sink};
        let exporter: Option<Box<dyn Sink>> = None;
        #[cfg(feature = "otlp")]
        let exporter = config
            .telemetry
            .as_ref()
            .map(|c| {
                c.build_sink("sdk-host")
                    .map(|s| Box::new(s) as Box<dyn Sink>)
            })
            .transpose()?
            .or(exporter);
        let export_content = config
            .telemetry
            .as_ref()
            .map(|c| c.content.clone())
            .unwrap_or_default();
        let (recorder, controller) = Recorder::start(
            options.open()?,
            options.deployment.clone(),
            options.queue_capacity,
            options.retention_days,
        )?;
        let statistics = recorder.statistics();
        let sink = RecordingSink::new(
            recorder,
            options.deployment.clone(),
            "sdk-host".into(),
            exporter,
            export_content.capture,
        )?;
        let (telemetry, telemetry_controller) = Telemetry::start_with_content(
            sink,
            2048,
            ContentPolicy {
                capture: export_content.capture.max(Capture::Metadata),
                ..export_content
            },
        )?;
        #[cfg(feature = "evaluation-export")]
        let evaluation_exporter = crate::evaluation_export::start(options.clone())?;
        return Ok(Started {
            #[cfg(feature = "evaluation-export")]
            evaluation_exporter,
            telemetry,
            controller: Some(telemetry_controller),
            recording: Some(std::sync::Arc::new(Recording {
                options: options.clone(),
                statistics,
                controller: std::sync::Mutex::new(Some(controller)),
            })),
        });
    }
    let (telemetry, controller) = match &config.telemetry {
        #[cfg(feature = "otlp")]
        Some(c) => {
            let (t, c) = c.start("sdk-host")?;
            (t, Some(c))
        }
        #[cfg(not(feature = "otlp"))]
        Some(_) => anyhow::bail!("Telemetry exporter not included"),
        None => (Telemetry::default(), None),
    };
    Ok(Started {
        #[cfg(feature = "evaluation-export")]
        evaluation_exporter: None,
        telemetry,
        controller,
        #[cfg(feature = "recording")]
        recording: None,
    })
}

#[cfg(feature = "recording")]
pub(crate) struct Recording {
    pub options: crate::RecordingConfig,
    pub statistics: openworker_observability::Statistics,
    pub controller: std::sync::Mutex<Option<openworker_observability::Controller>>,
}
#[cfg(feature = "recording")]
impl crate::RecordingConfig {
    pub(crate) fn open(&self) -> Result<Box<dyn openworker_observability::Store>> {
        self.validate()?;
        match &self.database {
            crate::RecordingDatabase::Sqlite { path } => Ok(Box::new(
                openworker_observability::sqlite::Sqlite::open(path)?,
            )),
            #[cfg(feature = "recording-postgres")]
            crate::RecordingDatabase::Postgres { .. } => Ok(Box::new(
                openworker_observability::postgres::Postgres::open(&self.postgres()?)?,
            )),
            #[cfg(not(feature = "recording-postgres"))]
            crate::RecordingDatabase::Postgres { .. } => {
                anyhow::bail!("PostgreSQL recording not included")
            }
        }
    }
}

#[cfg(feature = "recording-postgres")]
impl crate::RecordingConfig {
    fn postgres(&self) -> Result<openworker_observability::postgres::Config> {
        use openworker_observability::postgres::{Config, Tls};
        let crate::RecordingDatabase::Postgres {
            connection_env,
            schema,
            ca_file,
            loopback_plaintext,
        } = &self.database
        else {
            anyhow::bail!("PostgreSQL recording configuration required")
        };
        self.validate()?;
        let mut c = Config::new(
            std::env::var(connection_env)
                .map_err(|_| anyhow::anyhow!("Recording database credential unavailable"))?,
            schema.clone(),
        );
        c.tls = if *loopback_plaintext {
            Tls::LoopbackPlaintext
        } else {
            Tls::Verified {
                ca_file: ca_file.clone(),
            }
        };
        Ok(c)
    }
    /// Explicit trusted setup operation. Host startup never migrates PostgreSQL.
    pub async fn migrate_postgres(&self) -> Result<()> {
        let options = self.clone();
        tokio::task::spawn_blocking(move || {
            openworker_observability::postgres::Postgres::migrate(&options.postgres()?)
        })
        .await?
    }
}
