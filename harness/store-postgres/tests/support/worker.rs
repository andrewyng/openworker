//! Development fault-injection worker. Never ship this test executable.
use openworker_runtime::{execution::Backend, model::Model, runtime::Runtime, store::StateStore};
use openworker_store_postgres::{Config, PostgresStore, Tls};
use openworker_tools::{Tool, ToolContext, ToolDescriptor, ToolFuture, ToolOutput, ToolRegistry};
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};
// Fixture-only metadata capture, flushed before graceful worker exit. Never ship
// this executable or include prompt/tool content in qualification output.
struct Capture(PathBuf);
impl openworker_runtime::telemetry::Sink for Capture {
    fn export(
        &mut self,
        records: &[openworker_runtime::telemetry::Record],
        _: openworker_runtime::telemetry::Snapshot,
    ) -> bool {
        use std::io::Write;
        let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.0)
        else {
            return false;
        };
        for r in records {
            let value = json!({"operation":r.operation.name(),"outcome":r.outcome.name(),"context":r.context,"links":r.links,"turn_hash":r.turn_hash,"timing_known":r.timing_known});
            if file.write_all(format!("{value}\n").as_bytes()).is_err() {
                return false;
            }
        }
        file.sync_all().is_ok()
    }
}
struct Probe;
impl Tool for Probe {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: "test.probe".into(),
            version: "1".into(),
            description: "Test marker and inspection".into(),
            input_schema: json!({"type":"object","properties":{"action":{"type":"string","enum":["write_wait","write_once","write_gate","inspect"]}},"required":["action"],"additionalProperties":false}),
        }
    }
    fn call(&self, ctx: ToolContext, args: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            if args["action"] == "write_gate" {
                use std::io::Write;
                let mut f = std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(ctx.workspace.join("effects.jsonl"))
                    .unwrap();
                writeln!(f,"{}",json!({"session_id":ctx.session_id,"turn_id":ctx.turn_id,"call_id":ctx.call_id,"pid":std::process::id()})).unwrap();
                f.sync_all().unwrap();
                // Test-controlled completion barrier, not a tool timeout. The
                // effect is durable BEFORE any completion/result can be saved.
                loop {
                    tokio::select! {biased;
                        _=ctx.cancellation.cancelled()=>return Err(openworker_tools::ToolError::cancelled()),
                        _=tokio::time::sleep(Duration::from_millis(30))=>{}
                    }
                    if ctx.workspace.join("release").exists() {
                        return Ok(ToolOutput::json(
                            json!({"written":true,"session_id":ctx.session_id,"turn_id":ctx.turn_id}),
                        ));
                    }
                }
            }
            let path = ctx.workspace.join("proof.txt");
            if args["action"] == "write_wait" || args["action"] == "write_once" {
                use std::io::Write;
                let mut f = std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(path)
                    .unwrap();
                writeln!(f, "one real side effect").unwrap();
                f.sync_all().unwrap();
                if args["action"] == "write_once" {
                    return Ok(ToolOutput::json(json!({"written":true})));
                }
                // Parent deliberately kills this process before any result commit.
                ctx.cancellation.cancelled().await;
                return Err(openworker_tools::ToolError::cancelled());
            }
            Ok(ToolOutput::json(
                json!({"observed":std::fs::read_to_string(path).unwrap()}),
            ))
        })
    }
}
#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> anyhow::Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let root = PathBuf::from(&args[0]);
    let (telemetry, recording) = openworker_runtime::telemetry::Telemetry::start(
        Capture(root.join("observations.jsonl")),
        2048,
    )?;
    let mut c = Config::new(std::env::var("OW_PG_TEST_URL")?, args[1].clone());
    c.tls = Tls::LoopbackPlaintext;
    c.lease_duration = Duration::from_secs(3);
    if args
        .get(4)
        .is_some_and(|v| v == "service" || v == "drain-service")
    {
        use openworker_store_postgres::worker::{Options, Selector, Worker};
        let load_profile = std::env::var("OW_FIXTURE_LOAD_PROFILE").as_deref() == Ok("1");
        if load_profile {
            c.lease_duration = Duration::from_secs(30);
        }
        c.timeout = if load_profile {
            Duration::from_secs(5)
        } else {
            Duration::from_millis(500)
        };
        let api = PostgresStore::open(c.clone())?;
        let sample = api.inspect(&args[3])?;
        let selector = Selector {
            coworker: sample.coworker,
            bundle_digest: sample.bundle_digest,
            backend: Backend::Off,
            scripted: true,
            fallback_model: "fixture".into(),
            model_provider: sample.model_provider,
            model_connections: Default::default(),
            environment_provider: None,
            environment_identity: None,
            environment_profile: None,
        };
        let label = args[2].clone();
        let marker = root.join(format!("{label}-draining"));
        let factory = std::sync::Arc::new(move |sid: &str, store: PostgresStore| {
            let mut tools = ToolRegistry::default();
            tools.register(Probe)?;
            Runtime::open_instrumented(
                &root.join(&label).join(sid),
                &root.join("bundle"),
                &std::env::current_exe()?,
                Model::new("fixture".into(), true),
                Backend::Off,
                None,
                None,
                tools,
                Some(Box::new(store)),
                telemetry.clone(),
            )
        });
        let stop = tokio_util::sync::CancellationToken::new();
        let drain = openworker_store_postgres::worker::Drain::default();
        let draining = args[4] == "drain-service";
        let requested = drain.clone();
        let signal = stop.clone();
        tokio::spawn(async move {
            #[cfg(unix)]
            {
                let mut term =
                    tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                        .unwrap();
                tokio::select! {_=tokio::signal::ctrl_c()=>{},_=term.recv()=>{}}
                if draining {
                    requested.request();
                } else {
                    signal.cancel();
                    return;
                }
                tokio::select! {_=tokio::signal::ctrl_c()=>{},_=term.recv()=>{}}
            }
            #[cfg(not(unix))]
            {
                let _ = tokio::signal::ctrl_c().await;
                if draining {
                    requested.request();
                    let _ = tokio::signal::ctrl_c().await;
                }
            }
            signal.cancel();
        });
        let acknowledged = drain.clone();
        tokio::spawn(async move {
            acknowledged.acknowledged().await;
            std::fs::write(marker, "acknowledged").unwrap();
        });
        let worker = Worker::new(
            c,
            selector,
            Options {
                slots: args
                    .get(5)
                    .map(|n| n.parse::<usize>())
                    .transpose()?
                    .unwrap_or(1),
                poll_interval: Duration::from_millis(if load_profile { 250 } else { 100 }),
                shutdown_timeout: Duration::from_secs(10),
            },
            factory,
        )?;
        let result = if draining {
            worker
                .run_with_drain(
                    drain,
                    stop,
                    Duration::from_secs(
                        args.get(6)
                            .map(|v| v.parse::<u64>())
                            .transpose()?
                            .unwrap_or(20),
                    ),
                )
                .await
        } else {
            worker.run(stop).await
        };
        anyhow::ensure!(
            recording.shutdown(Duration::from_secs(3)),
            "Fixture recording did not flush"
        );
        return result;
    }
    let store = PostgresStore::open(c)?;
    if let Some(sid) = args.get(3) {
        store.claim(sid)?;
    }
    // Fixture-owned heartbeat, not a runtime feature. Startup hashes the runner
    // and can exceed a deliberately short test lease on a constrained host.
    let heartbeat = store.clone();
    std::thread::spawn(move || loop {
        for session in heartbeat.list_metadata().expect("fixture ownership lost") {
            heartbeat
                .renew(&session.id)
                .expect("fixture renewal failed");
        }
        std::thread::sleep(Duration::from_millis(250));
    });
    let mut tools = ToolRegistry::default();
    tools.register(Probe)?;
    let runtime = Runtime::open_instrumented(
        &root.join(&args[2]),
        &root.join("bundle"),
        &std::env::current_exe()?,
        Model::new("fixture".into(), true),
        Backend::Off,
        None,
        None,
        tools,
        Some(Box::new(store.clone())),
        telemetry,
    )?;
    // Fixture-only queue pump. A production scheduler must manage lease loss,
    // backoff, metrics and compatible environment placement separately.
    let pump = if args.get(4).is_some_and(|v| v == "consume") {
        let rt = runtime.clone();
        Some(tokio::spawn(async move {
            loop {
                for session in store.list_metadata().expect("fixture ownership lost") {
                    if let Some(control) = store
                        .next_control(&session.id)
                        .expect("fixture control peek failed")
                    {
                        let _ = rt
                            .apply_control(&session.id, &control.command.request_id)
                            .await;
                        continue;
                    }
                    if let Some(request) =
                        store.next_turn(&session.id).expect("fixture peek failed")
                    {
                        // Re-read durable state next tick after a transient busy
                        // response; never pop first or replay an accepted command.
                        let _ = rt
                            .dispatch("turn/start", serde_json::to_value(request).unwrap())
                            .await;
                    }
                }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        }))
    } else {
        None
    };
    let result = openworker_runtime::protocol::serve(runtime).await;
    // The fixture pump must stop before the Tokio timer driver is torn down.
    // Otherwise an EOF exit can race its next sleep and panic after assertions.
    if let Some(pump) = pump {
        pump.abort();
        let _ = pump.await;
    }
    anyhow::ensure!(
        recording.shutdown(Duration::from_secs(3)),
        "Fixture recording did not flush"
    );
    result
}
