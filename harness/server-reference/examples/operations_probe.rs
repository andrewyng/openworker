//! Local fault qualification only. No provider, shell, user data or live tools.
//! Uses production snapshot sampler + worker heartbeat; fixtures are synthetic.
use anyhow::{bail, ensure, Result};
use openworker_runtime::{
    execution::Backend,
    store::{Session, StateStore},
};
use openworker_store_postgres::{Config, PostgresStore, Tls, TurnSubmission};
use openworker_telemetry::{Operation, Outcome};
use serde_json::json;
use std::{sync::Arc, time::Duration};
use tokio_util::sync::CancellationToken;

#[tokio::main]
async fn main() -> Result<()> {
    let mode = std::env::args().nth(1).unwrap_or_default();
    let schema = std::env::var("OW_OPS_SCHEMA")?;
    ensure!(
        schema.starts_with("ow_ops_")
            && schema.len() == 39
            && schema[7..].bytes().all(|b| b.is_ascii_hexdigit()),
        "Explicit isolated qualification schema required"
    );
    let mut c = Config::new(std::env::var("OW_PG_TEST_URL")?, schema.clone());
    c.tls = Tls::LoopbackPlaintext;
    c.timeout = Duration::from_secs(2);
    let stop = CancellationToken::new();
    let signal = stop.clone();
    tokio::spawn(async move {
        let _ = tokio::signal::ctrl_c().await;
        signal.cancel();
    });
    if mode == "sample" || mode == "worker" || mode == "errors" {
        let config = openworker_telemetry::otlp::Config {
            endpoint: std::env::var("OW_OPS_ENDPOINT")?,
            deployment: schema.clone(),
            ..Default::default()
        };
        let role = if mode == "sample" { "api" } else { "worker" };
        let (t, controller) = tokio::task::spawn_blocking(move || config.start(role)).await??;
        c.telemetry = t.clone();
        match mode.as_str() {
            "sample" => {
                openworker_server_reference::operations::run(
                    c,
                    openworker_server_reference::operations::Options {
                        sample_interval_seconds: 1,
                        stalled_after_seconds: 10,
                        recent_window_seconds: 120,
                    },
                    t,
                    stop,
                )
                .await
            }
            "worker" => {
                use openworker_store_postgres::worker::{Options, Selector, Worker};
                let selector = Selector {
                    coworker: "never_matches_fixture".into(),
                    bundle_digest: "fixture".into(),
                    backend: Backend::Openshell,
                    scripted: true,
                    fallback_model: "fixture".into(),
                    model_provider: Default::default(),
                    model_connections: Default::default(),
                    environment_provider: Some("openshell".into()),
                    environment_identity: Some("fixture".into()),
                    environment_profile: Some("fixture".into()),
                };
                Worker::new(
                    c,
                    selector,
                    Options {
                        slots: 2,
                        ..Default::default()
                    },
                    Arc::new(|_, _| bail!("Qualification must never execute tools")),
                )?
                .run(stop)
                .await?;
            }
            _ => {
                // One error per operation, no zero counter baseline. Synthetic;
                // tests first-error detection, not provider behavior.
                t.event(Operation::Model, Outcome::Error, Some("SECRET"), None);
                t.event(Operation::Tool, Outcome::Error, Some("SECRET"), None);
                stop.cancelled().await;
            }
        }
        tokio::task::spawn_blocking(move || controller.shutdown(Duration::from_secs(5))).await?;
        return Ok(());
    }
    if mode == "seed" {
        PostgresStore::migrate(&c)?;
        let mut store = PostgresStore::open(c.clone())?;
        for (id, status) in [("s_active", "running"), ("s_waiting", "waiting")] {
            let s: Session = serde_json::from_value(
                json!({"id":id,"name":"SECRET","workspace":"/SECRET","coworker":"fixture","bundle_digest":"fixture","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()}),
            )?;
            store.save(&s, "session_created", json!({}))?;
            store.submit_turn(TurnSubmission {
                limits: None,
                session_id: id.into(),
                submission_id: "one".into(),
                text: "SECRET".into(),
            })?;
            let (mut s, _, _) = store.accept(id, "one", "SECRET")?;
            s.turn.as_mut().unwrap().status = status.into();
            if status == "waiting" {
                s.interactions = serde_json::from_value(
                    json!([{"id":"i_one","turn_id":s.turn.as_ref().unwrap().id,"kind":"approval","call":{"SECRET":"SECRET"},"bundle_digest":"fixture","schema_digest":"fixture","response":null,"resource_policy_digest":"fixture"}]),
                )?;
            }
            store.save(&s, "turn_recovered", json!({"outcome_unknown_count":1}))?;
            store.release(id)?;
        }
        store.submit_turn(TurnSubmission {
            limits: None,
            session_id: "s_waiting".into(),
            submission_id: "two".into(),
            text: "SECRET".into(),
        })?;
        return Ok(());
    }
    tokio::task::block_in_place(|| -> Result<()> {
        let mut db = postgres::Client::connect(&c.connection, postgres::NoTls)?;
        db.batch_execute(&format!("SET search_path={schema},pg_catalog"))?;
        match mode.as_str() {
        "clear" => db.batch_execute("UPDATE sessions SET data=jsonb_set(jsonb_set(data,'{turn,status}','\"completed\"'),'{interactions}', '[]'); UPDATE submissions SET status='completed'; UPDATE audit SET created=now()-interval '1 hour'")?,
        "break-sampler" => db.batch_execute("BEGIN; LOCK TABLE audit IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(4); COMMIT")?,
        "snapshot" => println!("{}",serde_json::to_string(&PostgresStore::open(c)?.operational_snapshot(10,20)?)?),
        "cleanup" => db.batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))?,
        _ => bail!("Expected seed/sample/worker/errors/clear/break-sampler/snapshot/cleanup"),
    }
        Ok(())
    })
}
