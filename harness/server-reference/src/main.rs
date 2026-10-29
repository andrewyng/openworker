use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use openworker_server_reference::Config;
use openworker_store_postgres::PostgresStore;
use std::{net::SocketAddr, path::PathBuf, sync::Arc};
use tokio_util::sync::CancellationToken;
#[derive(Parser)]
struct Args {
    #[arg(long)]
    config: PathBuf,
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    /// Reconcile durable release/profile provisioning operations. No public port.
    ServiceController,
    /// Private worker-pool reconciler, separate from OpenShell provisioning.
    ServicePlatformController,
    /// Schedule all admitted release/profile cohorts within one slot budget.
    ServiceWorker {
        #[arg(long, default_value_t = 4)]
        slots: usize,
        #[arg(long, default_value = "service-worker")]
        node: String,
        #[arg(long)]
        health_listen: Option<SocketAddr>,
        #[arg(long,default_value_t=300,value_parser=clap::value_parser!(u64).range(1..=3600))]
        drain_timeout_seconds: u64,
    },
    Migrate,
    /// Trusted installer aggregate check; no public/session API equivalent.
    ServiceInstallStatus,
    /// Trusted-host operational catalog, not a session-authorized API.
    WorkerPools {
        #[arg(long)]
        after: Option<String>,
        #[arg(long, default_value_t = 50, value_parser = clap::value_parser!(u16).range(1..=100))]
        limit: u16,
    },
    /// Local first-admin bootstrap. API never reads this file as a master key.
    AuthBootstrap {
        #[arg(long,default_value_t=3600,value_parser=clap::value_parser!(u64).range(60..=86400))]
        ttl_seconds: u64,
    },
    /// Issue immutable role/scope using current administrator key and private files.
    AuthIssue {
        #[arg(long)]
        spec: PathBuf,
        #[arg(long)]
        token_file: PathBuf,
        #[arg(long)]
        request_id: String,
    },
    /// Private infrastructure-admin recovery; no HTTP equivalent and no bootstrap reset.
    AuthRecover {
        #[arg(long)]
        request_id: String,
        #[arg(long,default_value_t=3600,value_parser=clap::value_parser!(u64).range(60..=86400))]
        ttl_seconds: u64,
    },
    AuthList,
    AuthRevoke {
        #[arg(long)]
        credential_id: String,
    },
    Api {
        #[arg(long)]
        node: String,
        #[arg(long)]
        listen: SocketAddr,
    },
    Worker {
        #[arg(long)]
        node: String,
        #[arg(long, default_value_t = 4)]
        slots: usize,
        /// Optional private readiness listener; must bind a loopback address.
        #[arg(long)]
        health_listen: Option<SocketAddr>,
        /// First termination signal stops intake and waits this long for active execution.
        #[arg(long, default_value_t = 300, value_parser = clap::value_parser!(u64).range(1..=3600))]
        drain_timeout_seconds: u64,
        #[arg(long, default_value_t = 45, value_parser = clap::value_parser!(u64).range(1..=120))]
        shutdown_timeout_seconds: u64,
    },
    EnvironmentCreate {
        #[arg(long)]
        request_id: String,
        #[arg(long)]
        shared: bool,
    },
    /// Inspect the durable original receipt after an uncertain provisioning result.
    EnvironmentInspect {
        #[arg(long)]
        environment_id: String,
    },
    /// Observe and bind the original remote object; never create a replacement.
    EnvironmentReconcile {
        #[arg(long)]
        environment_id: String,
        #[arg(long)]
        remote_name: String,
    },
}
#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    let mut config: Config = serde_json::from_slice(&openworker_runtime::bundle::read_limited(
        &args.config,
        64_000,
    )?)?;
    let role = match &args.command {
        Command::Api { .. } => "api",
        Command::Worker { .. } | Command::ServiceWorker { .. } => "worker",
        _ => "admin",
    };
    let recording_config = config.clone();
    let record = !matches!(args.command, Command::Migrate) && !config.recovery_read_only;
    let started = tokio::task::spawn_blocking(move || {
        openworker_server_reference::recording::start(&recording_config, role, record)
    })
    .await??;
    config.recording_stats = started.statistics;
    let result = run(args, config, started.telemetry).await;
    let controller = started.telemetry_controller;
    if let Some(controller) = controller {
        let _ = tokio::task::spawn_blocking(move || {
            let flushed = controller.shutdown(std::time::Duration::from_secs(5));
            let stats = controller.snapshot();
            if !flushed || stats.dropped > 0 || stats.export_failures > 0 {
                eprintln!(
                    "Telemetry shutdown: flushed={flushed}, dropped={}, failed_batches={}",
                    stats.dropped, stats.export_failures
                );
            }
        })
        .await;
    }
    // Drain the upstream dispatcher before closing the independent recording queue.
    if let Some(controller) = started.recorder_controller {
        let _=tokio::task::spawn_blocking(move||{
            let flushed=controller.shutdown(std::time::Duration::from_secs(5));
            let stats=controller.statistics().snapshot();
            if !flushed||stats.dropped>0||stats.failed_writes>0||stats.conflicts>0||stats.rejected>0||stats.abandoned>0 {
                eprintln!("Recording shutdown: flushed={flushed}, dropped={}, failed={}, conflicts={}, rejected={}, abandoned={}",stats.dropped,stats.failed_writes,stats.conflicts,stats.rejected,stats.abandoned);
            }
        }).await;
    }
    result
}
async fn run(args: Args, config: Config, telemetry: openworker_telemetry::Telemetry) -> Result<()> {
    if matches!(
        args.command,
        Command::Worker { .. }
            | Command::ServiceWorker { .. }
            | Command::ServiceController
            | Command::ServicePlatformController
            | Command::EnvironmentCreate { .. }
            | Command::EnvironmentReconcile { .. }
    ) {
        config.require_execution()?;
    }
    let mut db = config.database()?;
    db.telemetry = telemetry;
    if matches!(args.command, Command::Migrate) {
        PostgresStore::migrate(&db)?;
        if let Some(options) = &config.observability {
            let recording_database = options.database(&config)?;
            tokio::task::spawn_blocking(move || {
                openworker_observability::postgres::Postgres::migrate(&recording_database)
            })
            .await??;
        }
        println!("Schema ready");
        return Ok(());
    }
    let store = PostgresStore::open(db.clone())?;
    let stop = CancellationToken::new();
    let abort = CancellationToken::new();
    let signal = stop.clone();
    let interrupt = abort.clone();
    tokio::spawn(async move {
        #[cfg(unix)]
        {
            let mut term =
                tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                    .expect("SIGTERM handler");
            tokio::select! {_=tokio::signal::ctrl_c()=>{},_=term.recv()=>{}}
            signal.cancel();
            tokio::select! {_=tokio::signal::ctrl_c()=>{},_=term.recv()=>{}}
            interrupt.cancel();
        }
        #[cfg(not(unix))]
        {
            let _ = tokio::signal::ctrl_c().await;
            signal.cancel();
            let _ = tokio::signal::ctrl_c().await;
            interrupt.cancel();
        }
    });
    match args.command {
        Command::ServiceInstallStatus => {
            println!(
                "{}",
                serde_json::to_string(&store.service_install_status()?)?
            );
        }
        Command::AuthBootstrap { .. }
        | Command::AuthIssue { .. }
        | Command::AuthRecover { .. }
        | Command::AuthList
        | Command::AuthRevoke { .. } => {
            use openworker_store_postgres::auth::Action;
            let token = zeroize::Zeroizing::new(String::from_utf8(
                openworker_runtime::bundle::read_limited(&config.api_token_file, 4096)?,
            )?);
            let authorized = store.authorized(token.trim())?;
            let result = match args.command {
                Command::AuthBootstrap { ttl_seconds } => {
                    match authorized.authorize(Action::Admin, None, None) {
                        Ok(p) => serde_json::to_value(p)?,
                        Err(_) => {
                            let now = std::time::SystemTime::now()
                                .duration_since(std::time::UNIX_EPOCH)?
                                .as_secs();
                            let r = store.auth_bootstrap_expiring(
                                token.trim(),
                                &config.actor,
                                Some((now + ttl_seconds) as i64),
                            )?;
                            // Reconciliation never renews or re-enables the original token.
                            r
                        }
                    }
                }
                Command::AuthIssue {
                    spec,
                    token_file,
                    request_id,
                } => {
                    let spec = serde_json::from_slice(&openworker_runtime::bundle::read_limited(
                        &spec, 64000,
                    )?)?;
                    let key = zeroize::Zeroizing::new(String::from_utf8(
                        openworker_runtime::bundle::read_limited(&token_file, 4096)?,
                    )?);
                    authorized.auth_issue(&request_id, spec, key.trim())?
                }
                Command::AuthRecover {
                    request_id,
                    ttl_seconds,
                } => {
                    let now = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)?
                        .as_secs();
                    store.auth_recover(
                        &request_id,
                        token.trim(),
                        &config.actor,
                        (now + ttl_seconds) as i64,
                    )?
                }
                Command::AuthList => authorized.auth_list("")?,
                Command::AuthRevoke { credential_id } => authorized.auth_revoke(&credential_id)?,
                _ => unreachable!(),
            };
            println!("{}", serde_json::to_string(&result)?);
        }
        Command::ServicePlatformController => {
            let k = config
                .kubernetes
                .as_ref()
                .context("Kubernetes configuration required")?;
            let svc = config
                .service
                .as_ref()
                .context("Service configuration required")?;
            store.bind_service(&svc.id, &svc.namespace)?;
            while !stop.is_cancelled() {
                openworker_server_reference::kubernetes::reconcile(k, &store, &svc.id).await?;
                tokio::select! {_=stop.cancelled()=>break,_=tokio::time::sleep(std::time::Duration::from_secs(2))=>{}}
            }
        }
        Command::ServiceController => {
            config.require_execution()?;
            openworker_server_reference::management::controller(config, store, stop).await?;
        }
        Command::ServiceWorker {
            slots,
            node,
            health_listen,
            drain_timeout_seconds,
        } => {
            config.require_execution()?;
            openworker_server_reference::management::worker_with_options(
                config,
                store,
                slots,
                stop,
                abort,
                node,
                health_listen,
                drain_timeout_seconds,
            )
            .await?;
        }
        Command::Api { node, listen } => {
            if let Some(options) = &config.operational_metrics {
                options.validate()?;
                anyhow::ensure!(
                    config.telemetry.is_some(),
                    "Operational metrics require explicit telemetry configuration"
                );
            }
            if !listen.ip().is_loopback() {
                bail!("Reference API binds loopback only; place a trusted authenticated TLS ingress in front for deployment");
            }
            let runtime = config.api_runtime(&node, store.clone())?;
            let app = openworker_server_reference::http::App::new(
                &config,
                runtime.clone(),
                store,
                stop.clone(),
            )?;
            let listener = tokio::net::TcpListener::bind(listen).await?;
            println!("API {node} ready at http://{}", listener.local_addr()?);
            let monitor_stop = stop.child_token();
            let _monitor_guard = monitor_stop.clone().drop_guard();
            let monitor = config.operational_metrics.clone().map(|options| {
                tokio::spawn(openworker_server_reference::operations::run(
                    db.clone(),
                    options,
                    db.telemetry.clone(),
                    monitor_stop.clone(),
                ))
            });
            use std::future::IntoFuture;
            let mut serving = Box::pin(
                axum::serve(listener, app.router())
                    .with_graceful_shutdown(stop.clone().cancelled_owned())
                    .into_future(),
            );
            tokio::select! {
                result=&mut serving=>result?,
                _=stop.cancelled()=>tokio::time::timeout(std::time::Duration::from_secs(30),serving).await.context("API drain timed out; closing remaining streams")??,
            }
            runtime.shutdown().await?;
            monitor_stop.cancel();
            if let Some(task) = monitor {
                let _ = tokio::time::timeout(std::time::Duration::from_secs(6), task).await;
            }
        }
        #[cfg(not(windows))]
        Command::Worker {
            node,
            slots,
            health_listen,
            drain_timeout_seconds,
            shutdown_timeout_seconds,
        } => {
            use openworker_runtime::environment::{Management, Sharing};
            use openworker_store_postgres::worker::{Options, Selector, Worker};
            anyhow::ensure!(
                health_listen.is_none_or(|a| a.ip().is_loopback()),
                "Worker readiness must bind loopback"
            );
            let template = config.runtime(&format!("{node}-template"), store.clone())?;
            let coworker = template
                .dispatch("coworker/list", serde_json::json!({}))
                .await?["coworkers"][0]["id"]
                .as_str()
                .context("Coworker unavailable")?
                .to_owned();
            let env = config
                .provider()?
                .spec(Sharing::Exclusive, Management::Harness);
            let selector = Selector {
                coworker,
                bundle_digest: template.execution_digest().into(),
                backend: openworker_runtime::execution::Backend::Openshell,
                scripted: config.scripted,
                fallback_model: config.model.clone(),
                model_provider: config.model_provider,
                model_connections: config
                    .model_connections
                    .iter()
                    .map(|c| {
                        let selected = template.connections.resolve(
                            &openworker_runtime::connections::Choice {
                                connection: c.id.clone(),
                                model: "validation".into(),
                            },
                        )?;
                        Ok((c.id.clone(), selected.identity))
                    })
                    .collect::<anyhow::Result<_>>()?,
                environment_provider: Some(env.provider),
                environment_identity: Some(env.provider_identity),
                environment_profile: Some(env.profile_digest),
            };
            template.shutdown().await?;
            drop(template);
            let provider = config.provider()?;
            // Required before admitting any session; config parsing alone does
            // not prove that mTLS credentials/version/gateway are usable.
            tokio::time::timeout(std::time::Duration::from_secs(10), provider.check_gateway())
                .await??;
            let pool_node = node.clone();
            let factory = Arc::new(move |sid: &str, store: PostgresStore| {
                config.runtime(
                    &format!(
                        "{node}-{}",
                        &openworker_runtime::bundle::digest(sid.as_bytes())[..16]
                    ),
                    store,
                )
            });
            let drain = openworker_store_postgres::worker::Drain::default();
            let health = openworker_server_reference::readiness::Readiness::new(drain.clone());
            let health_stop = CancellationToken::new();
            let health_server = if let Some(address) = health_listen {
                let listener = tokio::net::TcpListener::bind(address).await?;
                let router = health.router();
                let done = health_stop.clone();
                Some(tokio::spawn(async move {
                    axum::serve(listener, router)
                        .with_graceful_shutdown(done.cancelled_owned())
                        .await
                }))
            } else {
                None
            };
            let requested = drain.clone();
            let acknowledgement = drain.clone();
            let drain_signal = tokio::spawn(async move {
                stop.cancelled().await;
                requested.request();
            });
            let notice = tokio::spawn(async move {
                acknowledgement.acknowledged().await;
                eprintln!("Worker draining: new admissions stopped; waiting for current execution to finish or park");
            });
            let worker = Worker::new(
                db,
                selector.clone(),
                Options {
                    slots,
                    shutdown_timeout: std::time::Duration::from_secs(shutdown_timeout_seconds),
                    ..Default::default()
                },
                factory,
            )?;
            store.advertise_worker(&pool_node, selector.clone(), slots as u16, false)?;
            health.checked();
            let run = worker.run_with_drain(
                drain.clone(),
                abort.clone(),
                std::time::Duration::from_secs(drain_timeout_seconds),
            );
            tokio::pin!(run);
            // Separate connection from slot leases. An unhealthy dependency
            // interrupts this worker; supervision restarts, never silently
            // reconnects a poisoned connection or replays a tool.
            let monitor = async {
                let mut tick = tokio::time::interval(std::time::Duration::from_secs(5));
                tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
                loop {
                    tick.tick().await;
                    tokio::time::timeout(
                        std::time::Duration::from_secs(10),
                        provider.check_gateway(),
                    )
                    .await??;
                    store.advertise_worker(
                        &pool_node,
                        selector.clone(),
                        slots as u16,
                        drain.is_requested(),
                    )?;
                    health.checked();
                }
                #[allow(unreachable_code)]
                anyhow::Ok(())
            };
            let result = tokio::select! {
                r=&mut run=>r,
                r=monitor=>{
                    health.clear();abort.cancel();
                    let _ = (&mut run).await;
                    r.context("Worker dependency readiness lost")
                }
            };
            health.clear();
            let _ = store.withdraw_worker();
            health_stop.cancel();
            if let Some(mut task) = health_server {
                if tokio::time::timeout(std::time::Duration::from_secs(3), &mut task)
                    .await
                    .is_err()
                {
                    task.abort();
                }
            }
            drain_signal.abort();
            notice.abort();
            result?;
        }
        Command::WorkerPools { after, limit } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&store.worker_pools(after.as_deref(), limit)?)?
            );
        }
        #[cfg(not(windows))]
        Command::EnvironmentCreate { request_id, shared } => {
            use openworker_runtime::environment::{Management, Provider, Sharing};
            let provider = config.provider()?;
            let (record, fresh) = store.begin_environment(
                &request_id,
                &config.actor,
                provider.spec(
                    if shared {
                        Sharing::Shared
                    } else {
                        Sharing::Exclusive
                    },
                    Management::Harness,
                ),
            )?;
            if !fresh {
                println!("{}", serde_json::to_string(&record)?);
                return Ok(());
            }
            match provider.create(record.clone()).await {
                Ok(binding) => println!(
                    "{}",
                    serde_json::to_string(&store.complete_environment(
                        &record.id,
                        record.revision,
                        Some(binding)
                    )?)?
                ),
                Err(e) => {
                    let _ = store.complete_environment(&record.id, record.revision, None);
                    return Err(e.context(
                        "Inspect the original environment; do not repeat creation with a new ID",
                    ));
                }
            }
        }
        Command::EnvironmentInspect { environment_id } => {
            use openworker_runtime::store::StateStore;
            println!(
                "{}",
                serde_json::to_string(&store.environment(&environment_id)?)?
            );
        }
        #[cfg(not(windows))]
        Command::EnvironmentReconcile {
            environment_id,
            remote_name,
        } => {
            use openworker_runtime::store::StateStore;
            let record = store.environment(&environment_id)?;
            let binding = config
                .provider()?
                .bind_existing(&record, &remote_name)
                .await?;
            println!(
                "{}",
                serde_json::to_string(&store.complete_environment(
                    &record.id,
                    record.revision,
                    Some(binding)
                )?)?
            );
        }
        #[cfg(windows)]
        Command::Worker { .. }
        | Command::EnvironmentCreate { .. }
        | Command::EnvironmentReconcile { .. } => {
            bail!("This server reference requires the qualified Kubernetes provider")
        }
        Command::Migrate => unreachable!(),
    }
    Ok(())
}

#[cfg(test)]
mod command_tests {
    use super::*;
    #[test]
    fn worker_drain_configuration_is_explicit_and_bounded() {
        let args =
            Args::try_parse_from(["server", "--config", "unused", "worker", "--node", "test"])
                .unwrap();
        assert!(matches!(
            args.command,
            Command::Worker {
                drain_timeout_seconds: 300,
                shutdown_timeout_seconds: 45,
                ..
            }
        ));
        for value in ["0", "3601"] {
            assert!(Args::try_parse_from([
                "server",
                "--config",
                "unused",
                "worker",
                "--node",
                "test",
                "--drain-timeout-seconds",
                value
            ])
            .is_err());
        }
        assert!(Args::try_parse_from([
            "server",
            "--config",
            "unused",
            "worker",
            "--node",
            "test",
            "--shutdown-timeout-seconds",
            "121"
        ])
        .is_err());
    }
    #[tokio::test]
    async fn recovery_blocks_workers_and_environment_mutations_before_database_access() {
        for command in [
            Command::ServiceController,
            Command::ServicePlatformController,
            Command::ServiceWorker {
                slots: 1,
                node: "test".into(),
                health_listen: None,
                drain_timeout_seconds: 300,
            },
            Command::Worker {
                node: "test".into(),
                health_listen: None,
                slots: 1,
                drain_timeout_seconds: 300,
                shutdown_timeout_seconds: 45,
            },
            Command::EnvironmentCreate {
                request_id: "test".into(),
                shared: false,
            },
            Command::EnvironmentReconcile {
                environment_id: "env_test".into(),
                remote_name: "remote_test".into(),
            },
        ] {
            let mut config: Config =
                serde_json::from_str(include_str!("../config.example.json")).unwrap();
            config.recovery_read_only = true;
            let args = Args {
                config: "unused".into(),
                command,
            };
            let error = run(args, config, openworker_telemetry::Telemetry::default())
                .await
                .unwrap_err();
            assert_eq!(error.to_string(), "recovery_read_only");
        }
    }
    #[test]
    fn reconciliation_requires_both_explicit_original_identifiers() {
        assert!(Args::try_parse_from([
            "server",
            "--config",
            "private.json",
            "environment-reconcile",
            "--environment-id",
            "env_original"
        ])
        .is_err());
        let parsed = Args::try_parse_from([
            "server",
            "--config",
            "private.json",
            "environment-reconcile",
            "--environment-id",
            "env_original",
            "--remote-name",
            "original-sandbox",
        ])
        .unwrap();
        assert!(
            matches!(parsed.command,Command::EnvironmentReconcile{environment_id,remote_name} if environment_id=="env_original" && remote_name=="original-sandbox")
        );
    }
}
