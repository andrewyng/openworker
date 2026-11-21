use axum::{extract::Json, routing::post, Router};
use openworker_runtime::{
    execution::Backend,
    model::Model,
    runtime::Runtime,
    safety::{Action, Config as SafetyConfig, Guardrails, Rule},
};
use openworker_server_reference::{http::App, Config};
use openworker_store_postgres::{
    app_agents::Grants,
    auth::{Role, Scope, Spec},
    PostgresStore,
};
use serde_json::{json, Value};
use std::{path::Path, time::Duration};
use tokio_util::sync::CancellationToken;

const ADMIN: &str = "test-eval-admin-credential-aaaaaaaaaaaa";
const EDITOR: &str = "test-eval-editor-credential-bbbbbbbbbbb";
const OTHER: &str = "test-eval-other-credential-cccccccccccc";

async fn listen(router: Router) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    (url, task)
}

fn bundle(root: &Path, agent: &str) -> std::path::PathBuf {
    let path = root.join(agent);
    std::fs::create_dir_all(path.join("coworker")).unwrap();
    std::fs::write(
        path.join("coworker/manifest.md"),
        format!("---\nid: {agent}\nname: {agent}\nplanning: false\nmemory: false\nscheduling: false\n---\nEdit the synthetic test page."),
    )
    .unwrap();
    std::fs::write(path.join("tools.json"), r#"{"version":3,"tools":[]}"#).unwrap();
    path
}

async fn run(runtime: &std::sync::Arc<Runtime>, root: &Path, text: &str) -> Value {
    let workspace = tempfile::tempdir_in(root).unwrap();
    let session = runtime
        .dispatch(
            "session/create",
            json!({
                "name":runtime.coworker(),
                "coworker":runtime.coworker(),
                "workspace":workspace.path(),
                "model":{"connection":"fixture","model":"page-editor"},
            }),
        )
        .await
        .unwrap();
    let sid = session["session"]["id"].as_str().unwrap();
    runtime
        .dispatch(
            "turn/start",
            json!({
                "session_id":sid,
                "submission_id":"synthetic-task",
                "text":text,
            }),
        )
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            let session = runtime.store.lock().unwrap().metadata(sid).unwrap();
            if let Some(turn) = session
                .turn
                .filter(|turn| openworker_runtime::store::terminal(&turn.status))
            {
                assert_eq!(turn.status, "completed");
                break json!({"session_id": sid, "turn_id": turn.id});
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .unwrap()
}

/// Populate a disposable catalog using captured evidence from the two agents.
/// The normal queue worker scores every inserted job.
fn seed_volume(connection: &str, schema: &str, editor: &str, other: &str) {
    let mut client = postgres::Client::connect(connection, postgres::NoTls).unwrap();
    let mut tx = client.transaction().unwrap();
    tx.batch_execute(&format!("SET LOCAL search_path TO {schema},pg_catalog"))
        .unwrap();
    let groups = [
        "Account recovery",
        "Agent handoffs",
        "Billing questions",
        "Build diagnostics",
        "Document extraction",
        "Knowledge search",
        "Order fulfillment",
        "Page editor",
        "Policy guidance",
        "Support triage",
        "Tool reliability",
        "Vision checks",
    ];
    let suites = [
        "Core flows",
        "Edge cases",
        "Long responses",
        "Multilingual",
        "Recent failures",
        "Release gate",
        "Smoke suite",
        "Structured output",
    ];
    let mut position = 0i32;
    for group in groups {
        for suite in suites {
            let (owner, template) = if position < 84 {
                ("app_editor", editor)
            } else {
                ("app_other", other)
            };
            let cases = 4 + (position * 9) % 40;
            let dataset = format!("evalset_volume_{position:03}");
            let name = format!("{group} · {suite}");
            tx.execute(
                "INSERT INTO eval_datasets(id,owner,name,version) VALUES ($1,$2,$3,$4)",
                &[&dataset, &owner, &name, &cases],
            )
            .unwrap();
            tx.execute(
                "INSERT INTO eval_cases(id,dataset,version,source,evidence,check_spec)
                 SELECT $1||'_'||v,$1,v,jsonb_set(c.source,'{name}',to_jsonb($3::text||' · Case '||v)),c.evidence,c.check_spec
                 FROM generate_series(1,$4::integer) AS v
                 JOIN eval_cases c ON c.dataset=$2 AND c.version=CASE
                    WHEN $5 AND v=$4 AND mod($6,11)=0 THEN 3
                    WHEN $5 AND mod(v + $6,9)=0 THEN 2 ELSE 1 END",
                &[&dataset,&template,&name,&cases,&(owner=="app_editor"),&position],
            ).unwrap();
            let now = openworker_runtime::store::now_ms() as i64;
            tx.execute(
                "INSERT INTO eval_jobs(id,dataset,version,created_ms)
                 SELECT 'run_'||$1||'_'||n,$1,greatest(1,$2::integer-mod(n,4)),
                    $3::bigint-(n-1)::bigint*43200000-mod($4*17,96)::bigint*240000-120000
                 FROM generate_series(1,CASE WHEN $4=61 THEN 96 ELSE 14 END) AS n",
                &[&dataset, &cases, &now, &position],
            )
            .unwrap();
            position += 1;
        }
    }
    tx.commit().unwrap();
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_PG_TEST_URL and an optional browser fixture"]
async fn synthetic_agents_capture_and_score_through_the_cluster_api() {
    let connection = std::env::var("OW_PG_TEST_URL").unwrap();
    let env = "OW_TEST_EVALS_DATABASE";
    std::env::set_var(env, &connection);
    let root = tempfile::tempdir().unwrap();
    let path = root.path().canonicalize().unwrap();
    let label = uuid::Uuid::new_v4().simple().to_string();
    let schema = format!("ow_eval_{}", &label[..12]);
    let recording_schema = format!("ow_eval_obs_{}", &label[..12]);
    let installed = bundle(&path, "page-editor");
    let runner = path.join("fixture-runner");
    std::fs::write(&runner, b"\x7fELF-inert-fixture").unwrap();
    let safety = SafetyConfig {
        guardrails: Guardrails {
            user_input: vec![
                Rule::regex("fixture-ssn", r"999-99-0000", Action::Block),
                Rule::credit_card(Action::redact()),
            ],
            model_output: vec![Rule::credit_card(Action::redact())],
            ..Default::default()
        },
        ..Default::default()
    };
    let configuration = json!({
        "database_env": env, "schema": schema, "database_loopback_plaintext": true,
        "state_root":path, "bundle":installed, "runner":runner, "model":"unused",
        "api_token_file":"unused", "actor":"admin",
        "openshell":{"identity":"unused","endpoint":"https://127.0.0.1:1","certificates":"unused","workspace":"unused","image":"unused"},
        "service":{"id":"eval-qualification","namespace":"test","agent":"page-editor"},
        "safety":safety,
        "observability":{"schema":recording_schema}
    });
    let config: Config = serde_json::from_value(configuration.clone()).unwrap();
    let config_path = path.join("eval-service.json");
    std::fs::write(&config_path, configuration.to_string()).unwrap();
    let db = config.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let store = PostgresStore::open(db).unwrap();
    store.auth_bootstrap(ADMIN, "admin").unwrap();
    let admin = store.authorized(ADMIN).unwrap();
    for (name, token) in [("app_editor", EDITOR), ("app_other", OTHER)] {
        admin
            .issue_app_token(
                name,
                Spec {
                    principal: name.into(),
                    role: Role::App,
                    scope: Scope::Owned,
                    environments: vec![],
                    expires_at: Some((openworker_runtime::store::now_ms() / 1000 + 3600) as i64),
                },
                token,
                Grants::default(),
            )
            .unwrap();
    }

    let (model_url, model_task) = listen(Router::new().route("/v1/chat/completions", post(|Json(body): Json<Value>| async move {
        let request = body["messages"].as_array().unwrap().iter().rev()
            .find(|item| item["role"] == "user").unwrap()["content"].to_string();
        let response = if request.contains("OVERSIZE_FIXTURE") {
            "a".repeat(16500)
        } else if request.contains("REDACT_FIXTURE") {
            "Synthetic card 4111111111111111 removed from this response.".into()
        } else if request.contains("FAIL_FIXTURE") {
            "<header>Field Notes</header>\n<a href=\"/plans\">Plans</a>".into()
        } else {
            "<header>Field Notes</header>\n<a href=\"/pricing\">Plans</a>".into()
        };
        let chunk = json!({"choices":[{"index":0,"delta":{"content":response},"finish_reason":"stop"}]});
        ([("content-type","text/event-stream")], format!("data: {chunk}\n\ndata: [DONE]\n\n"))
    }))).await;

    let recording_config = config.clone();
    let started = tokio::task::spawn_blocking(move || {
        let database = recording_config
            .observability
            .as_ref()
            .unwrap()
            .database(&recording_config)
            .unwrap();
        openworker_observability::postgres::Postgres::migrate(&database).unwrap();
        openworker_server_reference::recording::start(&recording_config, "worker", true).unwrap()
    })
    .await
    .unwrap();
    let open = |name| {
        let mut runtime = Runtime::open_instrumented_optional_runner(
            &path.join(format!("{name}-runtime")),
            &bundle(&path, name),
            None,
            Model::new("page-editor".into(), false),
            Backend::Off,
            None,
            None,
            openworker_tools::ToolRegistry::default(),
            Some(Box::new(store.clone())),
            started.telemetry.clone(),
        )
        .unwrap();
        Runtime::configure_safety(&mut runtime, safety.clone(), Default::default()).unwrap();
        runtime
            .connections
            .set(json!({
                "id":"fixture","label":"Fixture","provider":"openai","api":"chat-completions",
                "base_url":format!("{model_url}/v1"),"api_key":"temporary-fixture-key",
                "persistence":"memory"
            }))
            .unwrap();
        runtime
    };
    let editor = open("page-editor");
    let other = open("second-editor");
    let pass = run(
        &editor,
        &path,
        "Update the Plans link to /pricing. Keep the header intact. sk-synthetic-test-secret",
    )
    .await;
    let fail = run(
        &editor,
        &path,
        "FAIL_FIXTURE: update the Plans link and preserve the header.",
    )
    .await;
    let error = run(
        &editor,
        &path,
        "OVERSIZE_FIXTURE: return a long synthetic document.",
    )
    .await;
    let foreign = run(&other, &path, "Keep this second app's result separate.").await;
    let redacted = run(
        &editor,
        &path,
        "REDACT_FIXTURE: return the synthetic test card.",
    )
    .await;
    let blocked_workspace = tempfile::tempdir_in(&path).unwrap();
    let blocked = editor
        .dispatch(
            "session/create",
            json!({
                "name":"Pre-admission safety fixture",
                "coworker":editor.coworker(),
                "workspace":blocked_workspace.path(),
                "model":{"connection":"fixture","model":"page-editor"},
            }),
        )
        .await
        .unwrap();
    let blocked_sid = blocked["session"]["id"].as_str().unwrap();

    // The disposable embedded runtimes write through the real PostgreSQL
    // journal. Assign the same ownership rows that service admission creates.
    let owner_connection = connection.clone();
    let owner_schema = schema.clone();
    let owners = [
        (pass.clone(), "app_editor"),
        (fail.clone(), "app_editor"),
        (error.clone(), "app_editor"),
        (foreign.clone(), "app_other"),
        (redacted.clone(), "app_editor"),
        (json!({"session_id":blocked_sid}), "app_editor"),
    ];
    tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&owner_connection, postgres::NoTls).unwrap();
        for (turn, owner) in owners {
            let sid = turn["session_id"].as_str().unwrap();
            pg.execute(
                &format!("INSERT INTO {owner_schema}.api_session_owners VALUES ($1,$2)"),
                &[&sid, &owner],
            )
            .unwrap();
        }
    })
    .await
    .unwrap();

    let stop = CancellationToken::new();
    let app = App::new(&config, editor.clone(), store.clone(), stop.clone()).unwrap();
    let (url, api_task) = listen(app.router()).await;
    let client = reqwest::Client::new();
    let send = |method, endpoint: String, key: &str, request_id: String| {
        client
            .request(method, format!("{url}/v1/evals/{endpoint}"))
            .bearer_auth(key)
            .header("x-openworker-namespace", "test")
            .header("Idempotency-Key", request_id)
    };
    let scoped = |method, endpoint: &str, key: &str| {
        client
            .request(method, format!("{url}/v1/{endpoint}"))
            .bearer_auth(key)
            .header("x-openworker-namespace", "test")
            .header("Idempotency-Key", "fixture-guardrails")
    };
    let denied = scoped(
        reqwest::Method::POST,
        &format!("sessions/{blocked_sid}/turns"),
        EDITOR,
    )
    .json(&json!({"text":"Use this synthetic identifier 999-99-0000"}))
    .send()
    .await
    .unwrap();
    assert_eq!(denied.status(), 422);
    let blocked = scoped(
        reqwest::Method::GET,
        "observability/guardrails?rule=fixture-ssn&outcome=blocked",
        EDITOR,
    )
    .send()
    .await
    .unwrap()
    .json::<Value>()
    .await
    .unwrap();
    assert_eq!(blocked["findings"].as_array().unwrap().len(), 1);
    assert_eq!(blocked["findings"][0]["session_id"], blocked_sid);
    assert_eq!(blocked["findings"][0]["turn_id"], Value::Null);
    assert_eq!(blocked["findings"][0]["tag"], "guardrail:fixture-ssn");
    assert!(!blocked.to_string().contains("999-99-0000"));
    let redaction_path = format!(
        "observability/guardrails?session_id={}&turn_id={}",
        redacted["session_id"].as_str().unwrap(),
        redacted["turn_id"].as_str().unwrap()
    );
    let finding = scoped(reqwest::Method::GET, &redaction_path, EDITOR)
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert_eq!(finding["findings"].as_array().unwrap().len(), 1);
    assert_eq!(finding["findings"][0]["outcome"], "redacted");
    assert_eq!(
        finding["findings"][0]["tag"],
        "guardrail:builtin.credit-card"
    );
    assert!(finding["findings"][0]["match_count"].as_u64().unwrap() > 0);
    assert!(!finding.to_string().contains("4111111111111111"));
    assert_eq!(
        scoped(reqwest::Method::GET, &redaction_path, OTHER)
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    let foreign_findings = scoped(reqwest::Method::GET, "observability/guardrails", OTHER)
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert_eq!(foreign_findings["findings"], json!([]));
    let evidence_path = |turn: &Value| {
        format!(
            "evidence/{}/{}",
            turn["session_id"].as_str().unwrap(),
            turn["turn_id"].as_str().unwrap()
        )
    };
    let captured = send(
        reqwest::Method::GET,
        evidence_path(&pass),
        EDITOR,
        "".into(),
    )
    .send()
    .await
    .unwrap();
    assert_eq!(captured.status(), 200);
    assert_eq!(captured.headers()["cache-control"], "no-store");
    let captured: Value = captured.json().await.unwrap();
    assert!(captured["output"].as_str().unwrap().contains("/pricing"));
    assert!(!captured.to_string().contains("synthetic-test-secret"));
    assert_eq!(
        send(reqwest::Method::GET, evidence_path(&pass), OTHER, "".into())
            .send()
            .await
            .unwrap()
            .status(),
        403
    );

    let mut dataset = String::new();
    for (idx, turn) in [&pass, &fail, &error].into_iter().enumerate() {
        let name = [
            "Update the pricing link",
            "Reject the old Plans link",
            "Handle a long response",
        ][idx];
        let body = json!({"dataset":"Link edits","name":name,"turn":turn,"include_input":false,
            "check":{"comparison":"contains","expected":"href=\"/pricing\""}});
        for _ in 0..2 {
            let res = send(
                reqwest::Method::POST,
                "cases".into(),
                EDITOR,
                format!("case-{idx}"),
            )
            .json(&body)
            .send()
            .await
            .unwrap();
            let status = res.status();
            let saved: Value = res.json().await.unwrap();
            assert_eq!(status, 200, "{saved}");
            assert_eq!(saved["dataset_version"], idx + 1);
            dataset = saved["dataset_id"].as_str().unwrap().to_owned();
        }
    }
    let jobs = format!("datasets/{dataset}/jobs");
    let mut queued = Value::Null;
    for _ in 0..2 {
        let response = send(reqwest::Method::POST, jobs.clone(), EDITOR, "run".into())
            .json(&json!({"dataset_version":3}))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), 202);
        queued = response.json().await.unwrap();
        assert_eq!(queued["status"], "queued");
    }
    let jobs = format!("{jobs}/{}", queued["id"].as_str().unwrap());
    assert_eq!(
        send(reqwest::Method::GET, jobs.clone(), OTHER, "".into())
            .send()
            .await
            .unwrap()
            .status(),
        404
    );

    let late_case = send(reqwest::Method::POST, "cases".into(), EDITOR, "late-case".into())
        .json(&json!({"dataset":"Link edits","turn":pass,"include_input":true,"check":{"comparison":"equals","expected":"Added after the run was queued."}}))
        .send().await.unwrap();
    assert_eq!(late_case.status(), 200);
    assert_eq!(
        late_case.json::<Value>().await.unwrap()["dataset_version"],
        4
    );

    let second_case = send(reqwest::Method::POST, "cases".into(), OTHER, "own-case".into())
        .json(&json!({"dataset":"Link edits","turn":foreign,"include_input":false,"check":{"comparison":"contains","expected":"Field Notes"}}))
        .send().await.unwrap();
    assert_eq!(second_case.status(), 200);
    let second_case = second_case.json::<Value>().await.unwrap();
    assert_ne!(second_case["dataset_id"], dataset);
    let own = send(reqwest::Method::GET, "datasets".into(), OTHER, "".into())
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert_eq!(own["datasets"].as_array().unwrap().len(), 1);
    assert_eq!(own["datasets"][0]["id"], second_case["dataset_id"]);

    let pending = send(
        reqwest::Method::GET,
        "runs?status=queued&limit=1".into(),
        EDITOR,
        "".into(),
    )
    .send()
    .await
    .unwrap()
    .json::<Value>()
    .await
    .unwrap();
    assert_eq!(pending["runs"][0]["id"], queued["id"]);
    assert_eq!(pending["runs"][0]["counts"], Value::Null);
    // Stop between committed batches and check that another process resumes
    // at the next ordinal. Put the checkpoint job first in the test queue.
    let checkpoint_dataset = "evalset_checkpoint";
    let checkpoint_id = "eval_checkpoint_job";
    let (checkpoint_connection, checkpoint_schema, copy_from) =
        (connection.clone(), schema.clone(), dataset.clone());
    tokio::task::spawn_blocking(move || {
        let mut pg = postgres::Client::connect(&checkpoint_connection,postgres::NoTls).unwrap();
        pg.batch_execute(&format!("SET search_path TO {checkpoint_schema},pg_catalog")).unwrap();
        pg.execute("INSERT INTO eval_datasets(id,owner,name,version) VALUES($1,'app_editor','Recovery checks',20)",
            &[&checkpoint_dataset]).unwrap();
        pg.execute("INSERT INTO eval_cases(id,dataset,version,source,evidence,check_spec)
            SELECT $1||'_'||n,$1,n,c.source,c.evidence,c.check_spec
            FROM generate_series(1,20) n JOIN eval_cases c ON c.dataset=$2 AND c.version=1",
            &[&checkpoint_dataset,&copy_from]).unwrap();
        pg.execute("INSERT INTO eval_jobs(id,dataset,version,created_ms) VALUES($1,$2,20,1)",
            &[&checkpoint_id,&checkpoint_dataset]).unwrap();
    }).await.unwrap();
    let checkpoint_store = PostgresStore::open(config.database().unwrap()).unwrap();
    assert!(checkpoint_store.score_next_eval().unwrap());
    drop(checkpoint_store);
    let checkpoint_path = format!("datasets/{checkpoint_dataset}/jobs/{checkpoint_id}");
    let progress = send(
        reqwest::Method::GET,
        checkpoint_path.clone(),
        EDITOR,
        "".into(),
    )
    .send()
    .await
    .unwrap()
    .json::<Value>()
    .await
    .unwrap();
    assert_eq!(progress["progress"], json!({"completed":16,"total":20}));
    assert!(progress["results"].is_null());

    let volume = std::env::var_os("OW_EVALS_VOLUME").is_some();
    if volume {
        let (connection, schema, dataset) = (connection.clone(), schema.clone(), dataset.clone());
        let other = second_case["dataset_id"].as_str().unwrap().to_owned();
        tokio::task::spawn_blocking(move || seed_volume(&connection, &schema, &dataset, &other))
            .await
            .unwrap();
    }

    // Restart the API while the work is still queued.
    api_task.abort();
    let _ = api_task.await;
    let listener = tokio::net::TcpListener::bind(url.trim_start_matches("http://"))
        .await
        .unwrap();
    let app = App::new(&config, editor.clone(), store.clone(), stop.clone()).unwrap();
    let api_task = tokio::spawn(async move {
        axum::serve(listener, app.router()).await.unwrap();
    });
    let pending_after_restart = send(reqwest::Method::GET, jobs.clone(), EDITOR, "".into())
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert_eq!(pending_after_restart["status"], "queued");

    let mut workers = Vec::new();
    let mut health_addresses = Vec::new();
    for _ in 0..2 {
        let probe = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let health = probe.local_addr().unwrap();
        drop(probe);
        health_addresses.push(format!("http://{health}/readyz"));
        workers.push(
            tokio::process::Command::new(env!("CARGO_BIN_EXE_openworker-server-reference"))
                .arg("--config")
                .arg(&config_path)
                .arg("eval-worker")
                .arg("--health-listen")
                .arg(health.to_string())
                .kill_on_drop(true)
                .spawn()
                .unwrap(),
        );
    }
    for health in health_addresses {
        tokio::time::timeout(Duration::from_secs(8), async {
            loop {
                if client
                    .get(&health)
                    .send()
                    .await
                    .is_ok_and(|r| r.status() == 204)
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        })
        .await
        .unwrap();
    }
    let finished: Value = tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            let res = send(reqwest::Method::GET, jobs.clone(), EDITOR, "".into())
                .send()
                .await
                .unwrap();
            let job: Value = res.json().await.unwrap();
            if job["status"] == "completed" {
                break job;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(finished["dataset_version"], 3);
    assert_eq!(finished["progress"], json!({"completed":3,"total":3}));
    assert_eq!(finished["results"].as_array().unwrap().len(), 3);
    assert_eq!(finished["results"][0]["result"]["score"]["value"], true);
    assert_eq!(finished["results"][1]["result"]["score"]["value"], false);
    assert_eq!(finished["results"][2]["result"]["status"], "error");

    let resumed: Value = tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            let result = send(
                reqwest::Method::GET,
                checkpoint_path.clone(),
                EDITOR,
                "".into(),
            )
            .send()
            .await
            .unwrap()
            .json::<Value>()
            .await
            .unwrap();
            if result["status"] == "completed" {
                break result;
            }
            tokio::time::sleep(Duration::from_millis(40)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(resumed["progress"], json!({"completed":20,"total":20}));
    let values = resumed["results"].as_array().unwrap();
    assert_eq!(values.len(), 20);
    for (index, item) in values.iter().enumerate() {
        assert_eq!(
            item["result"]["id"],
            format!("{checkpoint_id}-{}", index + 1)
        );
    }

    if volume {
        // Let the worker fill the historical catalog before capturing it in Chrome.
        tokio::time::timeout(Duration::from_secs(45), async {
            loop {
                let pending = send(
                    reqwest::Method::GET,
                    "runs?status=queued&limit=1".into(),
                    ADMIN,
                    "".into(),
                )
                .send()
                .await
                .unwrap()
                .json::<Value>()
                .await
                .unwrap();
                if pending["runs"] == json!([]) {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(150)).await;
            }
        })
        .await
        .unwrap();

        let all = send(
            reqwest::Method::GET,
            "runs?limit=2&status=completed".into(),
            ADMIN,
            "".into(),
        )
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
        assert_eq!(all["runs"].as_array().unwrap().len(), 2);
        assert!(all["runs"][0]["counts"].is_object());
        let after = all["next_cursor"].as_str().unwrap();
        let later = send(
            reqwest::Method::GET,
            format!("runs?limit=2&status=completed&after={after}"),
            ADMIN,
            "".into(),
        )
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
        assert_ne!(all["runs"][1]["id"], later["runs"][0]["id"]);
        let other_id = second_case["dataset_id"].as_str().unwrap();
        let foreign = send(
            reqwest::Method::GET,
            format!("runs?dataset={other_id}"),
            EDITOR,
            "".into(),
        )
        .send()
        .await
        .unwrap();
        assert_eq!(foreign.status(), 404);
    }

    if let Ok(playwright) = std::env::var("OW_PLAYWRIGHT_MODULE") {
        let result = tokio::process::Command::new("node")
            .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("../service-ui/test/evals-live.mjs"))
            .env("OW_PLAYWRIGHT_MODULE", playwright)
            .env("OW_EVALS_URL", &url)
            .env("OW_EVALS_TOKEN", EDITOR)
            .env("OW_EVALS_PASS_TURN", pass.to_string())
            .env("OW_GUARDRAIL_REDACTED_TURN", redacted.to_string())
            .env("OW_GUARDRAIL_BLOCKED_SESSION", blocked_sid)
            .env("OW_EVALS_DATASET", &dataset)
            .env("OW_EVALS_JOB", queued["id"].as_str().unwrap())
            .output()
            .await
            .unwrap();
        assert!(
            result.status.success(),
            "{} {}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
        print!("{}", String::from_utf8_lossy(&result.stdout));
    }

    stop.cancel();
    for mut worker in workers {
        worker.kill().await.unwrap();
    }
    editor.shutdown().await.unwrap();
    other.shutdown().await.unwrap();
    api_task.abort();
    model_task.abort();
}
