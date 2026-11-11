//! Standalone opt-in through the real release API, one worker and OpenShell.
use super::*;
use openworker_runtime::agent_team::Document;
use openworker_sdk::service::{Service, Session};
fn spawn(id: &str, tools: &[&str], context: String, execution: &str) -> Value {
    json!({"name":"create_sub_agent","arguments":{"agent_id":id,"system_prompt":"Perform only the briefed task. Report actual tool outcomes.","context":context,"tools":tools,"execution":execution}})
}
fn doc(config: &openworker_store_postgres::Config, sid: &str) -> Document {
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&config.connection, postgres::NoTls).unwrap();
        serde_json::from_value(pg.query_one(&format!("SELECT data FROM {}.agent_teams WHERE data->'roster'->'instances'->(data->>'lead')->>'session_id'=$1",config.schema),&[&sid]).unwrap().get(0)).unwrap()
    })
}
async fn metadata(
    config: &openworker_store_postgres::Config,
    sid: &str,
) -> openworker_runtime::store::Session {
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&config.connection, postgres::NoTls).unwrap();
        serde_json::from_value(
            pg.query_one(
                &format!("SELECT data FROM {}.sessions WHERE id=$1", config.schema),
                &[&sid],
            )
            .unwrap()
            .get(0),
        )
        .unwrap()
    })
}
pub(super) async fn qualify(
    service: &Service,
    parent: &Session,
    _db: &PostgresStore,
    worker: &mut Child,
    path: &Path,
    config: &openworker_store_postgres::Config,
) {
    let html="<!doctype html><title>Counter</title><button onclick=\"this.textContent=Number(this.textContent)+1\">0</button>";
    let steps = json!([
        spawn("builder",&["coding.write_file"],plan("write_file",json!({"path":"index.html","content":html})),"async"),
        spawn("notes",&["coding.write_file"],plan("write_file",json!({"path":"notes.txt","content":"Separate child artifact"})),"async"),
        {"name":"wait_agent","arguments":{"agent_ids":["builder","notes"],"timeout_ms":120000}},
        spawn("verifier",&["coding.read_file"],plan("read_file",json!({"path":"index.html"})),"sync")
    ]);
    let run = parent
        .send(
            &format!("TEST_RESOURCE_GRANTS {steps}"),
            Some("standalone-build"),
        )
        .await
        .unwrap();
    assert_eq!(run.result().await.unwrap().status, "completed");
    let original = doc(config, parent.id());
    assert!(original.standalone.is_some());
    assert_eq!(original.roster.instances().count(), 4);
    let root = metadata(config, parent.id()).await;
    for id in ["builder", "notes", "verifier"] {
        let child = original.roster.instance(id).unwrap();
        let state = metadata(config, &child.session_id).await;
        assert_eq!(
            state.environment.as_ref().unwrap().environment_id,
            root.environment.as_ref().unwrap().environment_id
        );
        assert_eq!(state.turn.as_ref().unwrap().status, "completed");
        assert!(state.package.standalone_subagents.is_none());
        let evidence = service
            .json(
                "GET",
                &format!("/v1/sessions/{}/events/page?after=0", child.session_id),
                None,
                None,
            )
            .await
            .unwrap();
        assert!(
            evidence["events"]
                .as_array()
                .unwrap()
                .iter()
                .any(|e| e["type"] == "tool_finished"),
            "{evidence}"
        );
        if id == "verifier" {
            assert!(evidence.to_string().contains("Counter"));
        }
    }
    // Replacement worker resolves the same durable children and inbox receipts.
    worker.kill().await.unwrap();
    let _ = worker.wait().await;
    *worker = Command::new(BIN)
        .arg("--config")
        .arg(path)
        .arg("service-worker")
        .args(["--slots", "1"])
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let continuation=parent.send(&plan("send_message",json!({"agent_id":"verifier","message":"Please confirm the previous evidence briefly."})),Some("standalone-follow-up")).await.unwrap();
    assert_eq!(continuation.result().await.unwrap().status, "completed");
    assert_eq!(
        doc(config, parent.id())
            .roster
            .instance("verifier")
            .unwrap()
            .session_id,
        original.roster.instance("verifier").unwrap().session_id
    );
    let identity = original
        .roster
        .instance("verifier")
        .unwrap()
        .session_id
        .clone();
    tokio::time::timeout(Duration::from_secs(60), async {
        loop {
            let s = metadata(config, &identity).await;
            if s.turn
                .as_ref()
                .is_some_and(|t| t.submission_id.starts_with("inbox-") && t.status == "completed")
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    let forbidden = spawn(
        "restricted",
        &["coding.read_file"],
        plan(
            "write_file",
            json!({"path":"forbidden.txt","content":"must not execute"}),
        ),
        "sync",
    );
    let rejected = parent
        .send(
            &format!("TEST_RESOURCE_GRANTS {}", json!([forbidden])),
            Some("forbidden-child-tool"),
        )
        .await
        .unwrap();
    assert_eq!(rejected.result().await.unwrap().status, "completed");
    let checked = doc(config, parent.id());
    assert!(
        checked.failures.is_empty(),
        "Child provisioning failed: {:?}; budget {:?}",
        checked.failures,
        checked.budget
    );
    assert_eq!(
        checked.roster.instance("restricted").unwrap().phase,
        openworker_runtime::team_coordination::Phase::Ready,
        "{checked:?}"
    );
    let restricted = checked
        .roster
        .instance("restricted")
        .unwrap()
        .session_id
        .clone();
    assert_eq!(
        metadata(config, &restricted).await.turn.unwrap().status,
        "failed"
    );
    let journal = service
        .json(
            "GET",
            &format!("/v1/sessions/{restricted}/events/page?after=0"),
            None,
            None,
        )
        .await
        .unwrap();
    assert!(!journal["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["type"] == "tool_started"));
    eprintln!("PASS standalone service releases: two async builders, sync restricted verifier, shared OpenShell, worker restart, durable follow-up");
}

pub(super) async fn live(
    service: &Service,
    parent: &Session,
    config: &openworker_store_postgres::Config,
) {
    let task="Build a small self-contained counter web app with exactly three child agents. First create_sub_agent agent_id=builder in async mode with tools=[coding.write_file,coding.read_file], instructing it to write index.html with a button and a visible count that increases on click, no external dependencies. Then create_sub_agent agent_id=notes in async mode with tools=[coding.write_file], instructing it to write usage.txt with one sentence explaining the button. Give both a short explicit system_prompt and context. Use wait_agent to await their completion notices. Then create_sub_agent agent_id=verifier in sync mode with tools=[coding.read_file], instructing it to read index.html and usage.txt, check the button and click behavior from the source, and report limitations without editing. Never create more children or rewrite these artifacts yourself. Return a concise summary after the verifier finishes. Do not run shell commands, install packages, contact external services or claim a browser test.";
    let run = parent
        .send(task, Some("standalone-live-counter"))
        .await
        .unwrap();
    let result = run.result().await.unwrap();
    assert_eq!(result.status, "completed");
    let state = doc(config, parent.id());
    assert_eq!(state.roster.instances().count(), 4);
    for id in ["builder", "notes", "verifier"] {
        let child = state
            .roster
            .instance(id)
            .expect("Expected child not created");
        let saved = metadata(config, &child.session_id).await;
        assert_eq!(saved.turn.as_ref().unwrap().status, "completed");
        let events = service
            .json(
                "GET",
                &format!("/v1/sessions/{}/events/page?after=0", child.session_id),
                None,
                None,
            )
            .await
            .unwrap();
        assert!(
            events["events"]
                .as_array()
                .unwrap()
                .iter()
                .any(|e| e["type"] == "tool_finished"),
            "Child did not execute a tool"
        );
        if id == "verifier" {
            let html = events["events"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|e| e["type"] == "tool_finished")
                .map(|e| tool_data(e).to_string())
                .collect::<String>();
            assert!(
                html.contains("<button")
                    && (html.contains("onclick") || html.contains("addEventListener"))
            );
            if let Ok(path) = std::env::var("OW_STANDALONE_ARTIFACT") {
                std::fs::write(path, html).unwrap();
            }
        }
    }
    assert!(state.budget.model_calls <= 30);
    assert!(state.budget.reported_output_tokens > 0);
    eprintln!("PASS bounded live standalone app, async builder and notes, sync verifier, {} model calls and {} output tokens",state.budget.model_calls,state.budget.reported_output_tokens);
}
