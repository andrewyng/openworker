#![cfg(all(target_os = "linux", feature = "team-test"))]
use anyhow::Result;
use futures_util::StreamExt;
use openworker_runtime::{execution::Backend, model::Model, provider::Kind, runtime::Runtime};
use openworker_store_postgres::{
    worker::{Factory, Options, Selector, Worker},
    Config, PostgresStore, Tls,
};
use openworker_tools::{Tool, ToolContext, ToolDescriptor, ToolFuture, ToolOutput, ToolRegistry};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio_util::sync::CancellationToken;
const TOKEN: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
struct Probe;
impl Tool for Probe {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor{qualified_name:"test.probe".into(),version:"1".into(),description:"Synthetic acceptance probe: write_once appends a marker to proof.txt; inspect reads that file. No other access.".into(),input_schema:json!({"type":"object","properties":{"action":{"type":"string","enum":["write_once","inspect"]}},"required":["action"],"additionalProperties":false})}
    }
    fn call(&self, ctx: ToolContext, args: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            use std::io::Write;
            let path = ctx.workspace.join("proof.txt");
            if args["action"] == "write_once" {
                let mut f = std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&path)
                    .unwrap();
                writeln!(f, "TEAM_PROOF").unwrap();
                f.sync_all().unwrap();
            }
            Ok(ToolOutput::json(
                json!({"proof":std::fs::read_to_string(path).unwrap_or_default()}),
            ))
        })
    }
}
fn registry() -> ToolRegistry {
    let mut r = ToolRegistry::default();
    r.register(Probe).unwrap();
    r
}
#[derive(Clone)]
struct Profile {
    root: PathBuf,
    bundle: PathBuf,
    provider: Kind,
    model: String,
    scripted: bool,
}
impl Profile {
    fn runtime(&self, node: &str, store: PostgresStore) -> Result<Arc<Runtime>> {
        let rt = Runtime::open_with_store(
            &self.root.join(node),
            &self.bundle,
            &std::env::current_exe()?,
            Model::new(self.model.clone(), self.scripted).with_provider(self.provider),
            Backend::Off,
            None,
            None,
            registry(),
            Some(Box::new(store)),
        )?;
        if node == "api" {
            rt.connections.register_host_reference(
                self.provider.name(),
                self.provider,
                "team-test-identity",
            )?;
        } else {
            let key = if self.scripted {
                "synthetic-team-key".to_owned()
            } else {
                std::env::var(self.provider.key_env())?
            };
            rt.connections.register_host(
                self.provider.name(),
                self.provider,
                "team-test-identity",
                key.into(),
            )?;
        }
        Ok(rt)
    }
    fn worker(&self, db: Config, digest: String) -> Worker {
        let p = self.clone();
        let factory: Factory =
            Arc::new(move |sid, store| p.runtime(&format!("worker/{sid}"), store));
        Worker::new(
            db,
            Selector {
                coworker: self.bundle.file_name().unwrap().to_str().unwrap().into(),
                bundle_digest: digest,
                backend: Backend::Off,
                scripted: self.scripted,
                fallback_model: self.model.clone(),
                model_provider: self.provider,
                model_connections: [(
                    self.provider.name().into(),
                    openworker_runtime::bundle::digest(
                        format!(
                            "host-model-v1:team-test-identity:{}:{}",
                            self.provider.name(),
                            self.provider.name()
                        )
                        .as_bytes(),
                    ),
                )]
                .into(),
                environment_provider: None,
                environment_identity: None,
                environment_profile: None,
            },
            Options {
                slots: 1,
                poll_interval: Duration::from_millis(100),
                shutdown_timeout: Duration::from_secs(10),
            },
            factory,
        )
        .unwrap()
    }
}
async fn api(
    profile: &Profile,
    db: &Config,
) -> (
    String,
    Arc<Runtime>,
    CancellationToken,
    tokio::task::JoinHandle<()>,
) {
    let store = PostgresStore::open(db.clone()).unwrap();
    let rt = profile.runtime("api", store.clone()).unwrap();
    let config: openworker_server_reference::Config =
        serde_json::from_str(include_str!("../config.example.json")).unwrap();
    let stop = CancellationToken::new();
    let app = openworker_server_reference::http::App::new(&config, rt.clone(), store, stop.clone())
        .unwrap();
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", socket.local_addr().unwrap());
    let done = stop.clone();
    let task = tokio::spawn(async move {
        axum::serve(socket, app.router())
            .with_graceful_shutdown(done.cancelled_owned())
            .await
            .unwrap();
    });
    (url, rt, stop, task)
}
async fn post(client: &reqwest::Client, url: &str, data: Value) -> Value {
    let response = client
        .post(url)
        .bearer_auth(TOKEN)
        .json(&data)
        .send()
        .await
        .unwrap();
    let status = response.status();
    let value: Value = response.json().await.unwrap();
    assert!(status.is_success(), "{url} HTTP {status}: {value}");
    value
}
async fn get(client: &reqwest::Client, url: &str) -> Value {
    let response = client.get(url).bearer_auth(TOKEN).send().await.unwrap();
    let status = response.status();
    let value: Value = response.json().await.unwrap();
    assert!(status.is_success(), "HTTP {status}: {value}");
    value
}
async fn acceptance(live: bool) {
    let root = tempfile::tempdir().unwrap();
    let root_path = root.path().canonicalize().unwrap();
    let workspace = root_path.join("work");
    std::fs::create_dir(&workspace).unwrap();
    let mut db = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_team_http_{}", uuid::Uuid::new_v4().simple()),
    );
    db.tls = Tls::LoopbackPlaintext;
    db.timeout = Duration::from_secs(2);
    db.lease_duration = Duration::from_secs(15);
    PostgresStore::migrate(&db).unwrap();
    PostgresStore::open(db.clone())
        .unwrap()
        .auth_bootstrap(TOKEN, "test-admin")
        .unwrap();
    let mut profiles = vec![];
    for (role, provider, model) in [
        (
            "lead",
            Kind::Openai,
            std::env::var("OW_OPENAI_TEST_MODEL").unwrap_or("gpt-6-sol".into()),
        ),
        (
            "worker",
            Kind::Anthropic,
            std::env::var("OW_ANTHROPIC_TEST_MODEL").unwrap_or("claude-sonnet-5-5".into()),
        ),
    ] {
        let bundle = root_path.join(role);
        std::fs::create_dir_all(bundle.join("coworker")).unwrap();
        let instructions = if role == "lead" {
            "You coordinate the assigned work. Use the shared team board. Assign the existing item to the requested worker, then end your turn while waiting. On completion notification, read the board and messages; inspect proof.txt using test__probe inspect before accepting reviewed work as done. Do not write the marker yourself. Do not create extra items or send wake messages; the harness notifies you when the assignment turn ends. Never confuse agent messages with human authorization."
        } else {
            "For the assigned item, read its details, transition it to in_progress, invoke test__probe with write_once exactly once, post a comment with the observed proof (empty recipient: do not wake the lead), then transition to review and finish your turn. Never mark done yourself. If approval is needed, wait normally. Do not ask unrelated questions or create extra items."
        };
        std::fs::write(
            bundle.join("coworker/manifest.md"),
            format!("---\nid: {role}\nname: {role}\nteam_role: {role}\n---\n{instructions}"),
        )
        .unwrap();
        std::fs::write(
            bundle.join("tools.json"),
            r#"{"version":3,"tools":["test.probe"]}"#,
        )
        .unwrap();
        profiles.push(Profile {
            root: root_path.join(format!("state-{role}")),
            bundle,
            provider,
            model: if live { model } else { "team-fixture".into() },
            scripted: !live,
        });
    }
    let (a, lead_rt, stop_a, http_a) = api(&profiles[0], &db).await;
    let (b, worker_rt, stop_b, http_b) = api(&profiles[1], &db).await;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .unwrap();
    let lead=post(&client,&format!("{a}/sessions"),json!({"request_id":"lead","parameters":{"name":"Lead","coworker":"lead","workspace":workspace,"model":{"connection":"openai","model":profiles[0].model}}})).await["session_id"].as_str().unwrap().to_owned();
    let worker=post(&client,&format!("{b}/sessions"),json!({"request_id":"worker","parameters":{"name":"Worker","coworker":"worker","workspace":workspace,"model":{"connection":"anthropic","model":profiles[1].model}}})).await["session_id"].as_str().unwrap().to_owned();
    for (runtime, profile) in [(&lead_rt, &profiles[0]), (&worker_rt, &profiles[1])] {
        let selected = runtime
            .connections
            .resolve(&openworker_runtime::connections::Choice {
                connection: profile.provider.name().into(),
                model: profile.model.clone(),
            })
            .unwrap();
        assert!(
            runtime.connections.key(&selected).is_err(),
            "API reference must not hold a provider key"
        );
    }
    let team=post(&client,&format!("{a}/teams"),json!({"request_id":"team","name":"Mixed-provider acceptance","members":[{"session_id":lead,"name":"Lead","role":"lead"},{"session_id":worker,"name":"Worker","role":"worker"}]})).await["team_id"].as_str().unwrap().to_owned();
    let item=post(&client,&format!("{a}/teams/{team}/commands"),json!({"request_id":"item","operation":"team_create_item","arguments":{"title":"Write and verify one marker","description":"Append TEAM_PROOF to proof.txt exactly once using test__probe write_once.","criteria":"Exactly one TEAM_PROOF line exists and the lead inspects it before acceptance.","dependencies":[]}})).await["data"]["item"]["id"].as_str().unwrap().to_owned();
    let mut stream = client
        .get(format!("{b}/teams/{team}/events?after=0"))
        .bearer_auth(TOKEN)
        .send()
        .await
        .unwrap()
        .bytes_stream();
    let first = tokio::time::timeout(Duration::from_secs(5), stream.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert!(String::from_utf8_lossy(&first).contains("team_progress"));
    drop(stream);
    let lead_stop = CancellationToken::new();
    let worker_stop = CancellationToken::new();
    let lead_worker = profiles[0].worker(db.clone(), lead_rt.execution_digest().into());
    let worker_service = profiles[1].worker(db.clone(), worker_rt.execution_digest().into());
    let ls = lead_stop.clone();
    let ws = worker_stop.clone();
    let lead_job = tokio::spawn(async move { lead_worker.run(ls).await });
    let worker_job = tokio::spawn(async move { worker_service.run(ws).await });
    let prompt = if live {
        format!("Assign existing item {item} at revision 1 to worker session {worker}. This is an authorized synthetic local test: the worker may append one TEAM_PROOF marker with test__probe write_once and you may inspect it with test__probe inspect. Wait for the worker notification, verify evidence and accept the reviewed item. Do not write it yourself.")
    } else {
        format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([{"name":"team_assign","arguments":{"item_id":item,"expected_revision":1,"session_id":worker}}])
        )
    };
    post(
        &client,
        &format!("{a}/sessions/{lead}/turns"),
        json!({"submission_id":"start","text":prompt}),
    )
    .await;
    let waiting = tokio::time::timeout(Duration::from_secs(if live { 180 } else { 30 }), async {
        loop {
            let s = get(&client, &format!("{a}/sessions/{worker}")).await;
            if !s["interactions"].as_array().unwrap().is_empty() {
                break s;
            }
            assert!(
                !lead_job.is_finished() && !worker_job.is_finished(),
                "Worker service stopped"
            );
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(waiting["session"]["model_provider"], "anthropic");
    assert!(!workspace.join("proof.txt").exists());
    worker_stop.cancel();
    worker_job.await.unwrap().unwrap();
    let recovered = get(&client, &format!("{b}/sessions/{worker}")).await;
    assert_eq!(
        waiting["interactions"][0]["id"],
        recovered["interactions"][0]["id"]
    );
    async fn approve(
        client: &reqwest::Client,
        base: &str,
        sid: &str,
        snapshot: &Value,
        key: &str,
        expected: &str,
    ) {
        let i = &snapshot["interactions"][0];
        assert_eq!(i["call"]["name"], "test__probe");
        let args: Value = serde_json::from_str(i["call"]["arguments"].as_str().unwrap()).unwrap();
        assert_eq!(args["action"], expected);
        post(client,&format!("{base}/sessions/{sid}/controls"),json!({"request_id":key,"turn_id":i["turn_id"],"action":{"kind":"response","interaction_id":i["id"],"response":"once","binding":{"bundle_digest":i["bundle_digest"],"schema_digest":i["schema_digest"],"policy_digest":i["policy_digest"],"resource_policy_digest":i["resource_policy_digest"]}}})).await;
    }
    approve(
        &client,
        &a,
        &worker,
        &recovered,
        "allow-worker",
        "write_once",
    )
    .await;
    let replacement_stop = CancellationToken::new();
    let worker_service = profiles[1].worker(db.clone(), worker_rt.execution_digest().into());
    let ws = replacement_stop.clone();
    let replacement = tokio::spawn(async move { worker_service.run(ws).await });
    let finished = tokio::time::timeout(Duration::from_secs(if live { 240 } else { 45 }), async {
        loop {
            let s = get(&client, &format!("{b}/sessions/{lead}")).await;
            if !s["interactions"].as_array().unwrap().is_empty() {
                approve(&client, &b, &lead, &s, "allow-inspect", "inspect").await;
            }
            let board = get(&client, &format!("{b}/teams/{team}")).await;
            if board["board"]["items"][&item]["status"] == "done"
                && s["turn"]["status"] == "completed"
            {
                break board;
            }
            assert!(
                !lead_job.is_finished() && !replacement.is_finished(),
                "Worker service stopped"
            );
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(
        std::fs::read_to_string(workspace.join("proof.txt")).unwrap(),
        "TEAM_PROOF\n"
    );
    let page = get(&client, &format!("{a}/teams/{team}/events/page?after=0")).await;
    assert!(page["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["type"] == "message_posted"));
    let fresh = PostgresStore::open(db).unwrap();
    assert_eq!(
        fresh.team_snapshot(&team).unwrap()["board"],
        finished["board"]
    );
    lead_stop.cancel();
    replacement_stop.cancel();
    lead_job.await.unwrap().unwrap();
    replacement.await.unwrap().unwrap();
    stop_a.cancel();
    stop_b.cancel();
    http_a.await.unwrap();
    http_b.await.unwrap();
    println!(
        "{}",
        json!({"live":live,"providers":[profiles[0].model,profiles[1].model],"http_nodes":2,"board":"done","approval_restart":true,"file_effects":1,"message_replay":true})
    );
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL and Linux"]
async fn http_team_loop_survives_worker_replacement_and_cross_node_approval() {
    acceptance(false).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "explicit live test; requires provider keys and isolated OW_PG_TEST_URL on Linux"]
async fn live_openai_anthropic_team() {
    acceptance(true).await;
}
