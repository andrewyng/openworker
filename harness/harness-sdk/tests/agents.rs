#![cfg(all(feature = "test-support", target_os = "linux"))]
//! The friendly agents layer over real engine runs (scripted models, real
//! tools, approvals, teams). No API keys.
#[path = "support/tool.rs"]
mod tool;
use openworker_sdk::agents::*;
use serde_json::json;
use std::{path::PathBuf, time::Duration};

fn runner() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"))
}
fn workspace(root: &std::path::Path) -> PathBuf {
    let w = root.join("work");
    std::fs::create_dir_all(&w).unwrap();
    w
}
async fn harness(root: &std::path::Path, agents: Vec<Agent>) -> Harness {
    let mut b = Harness::builder()
        .state_dir(root.join("state"))
        .executable(runner())
        .tools(tool::registry())
        .sandbox(Sandbox::Off)
        .scripted(true);
    for a in agents {
        b = b.agent(a);
    }
    b.embedded().await.unwrap()
}
fn probe_plan() -> String {
    format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":"sdk__probe","arguments":{"block":false}}])
    )
}
async fn within<T>(f: impl std::future::Future<Output = T>) -> T {
    tokio::time::timeout(Duration::from_secs(60), f)
        .await
        .unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn run_interrupts_for_approval_then_resumes_to_final_output() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let agent = Agent::new("assistant")
        .instructions("Use the probe when asked.")
        .tool("sdk.probe");
    let h = harness(&root, vec![agent]).await;
    let s = h.session("assistant", workspace(&root)).await.unwrap();

    let r = within(s.run(&probe_plan())).await.unwrap();
    assert_eq!(r.status, RunStatus::Interrupted);
    assert_eq!(r.interruptions.len(), 1);
    let i = &r.interruptions[0];
    assert_eq!(i.agent, "assistant");
    assert!(matches!(&i.kind, InterruptionKind::Approval { tool, .. } if tool == "sdk__probe"));
    assert!(
        !root.join("work/proof.txt").exists(),
        "nothing runs before approval"
    );
    assert!(
        s.close().await.is_err(),
        "active sessions must not report a successful close"
    );

    // Answering an approval with text is rejected locally.
    assert!(s.respond(i, Response::Answer("yes".into())).await.is_err());
    s.respond(i, Response::Approve(ApprovalDecision::Once))
        .await
        .unwrap();
    s.respond(i, Response::Approve(ApprovalDecision::Once))
        .await
        .unwrap();
    assert!(
        s.respond(i, Response::Deny).await.is_err(),
        "a conflicting retry is not success"
    );
    let r = within(s.resume()).await.unwrap();
    assert_eq!(r.status, RunStatus::Completed);
    assert!(r
        .final_output
        .unwrap()
        .contains("Resource fixture finished"));
    assert_eq!(
        std::fs::read_to_string(root.join("work/proof.txt")).unwrap(),
        "called\n"
    );

    // The reference round-trips through serde and reattaches.
    let saved = serde_json::to_string(s.reference()).unwrap();
    let again = h
        .resume(serde_json::from_str(&saved).unwrap())
        .await
        .unwrap();
    assert_eq!(again.id(), s.id());
    h.shutdown().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn interrupt_on_lets_unlisted_tools_run_and_streams_progress() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let agent = Agent::new("assistant")
        .instructions("Use the probe when asked.")
        .tools(["sdk.probe", "test.probe"])
        .interrupt_on(["test.probe"]);
    let h = harness(&root, vec![agent]).await;
    let s = h.session("assistant", workspace(&root)).await.unwrap();

    let mut stream = s.run_streamed(&probe_plan()).await.unwrap();
    let mut auto = false;
    let mut message = false;
    within(async {
        while let Some(e) = stream.next().await {
            match e {
                RunEvent::AutoApproved { tool, .. } => auto |= tool == "sdk__probe",
                RunEvent::Message { .. } => message = true,
                RunEvent::Interrupted(i) => panic!("unexpected interruption {i:?}"),
                _ => {}
            }
        }
    })
    .await;
    let r = stream.finish().await.unwrap();
    assert_eq!(r.status, RunStatus::Completed);
    assert!(
        auto,
        "sdk.probe is not in interrupt_on, so the SDK approved it"
    );
    assert!(message);
    assert!(root.join("work/proof.txt").exists());
    h.shutdown().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn subagents_run_as_a_team_and_surface_worker_approvals() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let worker = Agent::new("worker")
        .description("Writes the proof file")
        .instructions("Do assigned work.")
        .tool("test.probe")
        .fallback_model(ProviderKind::Anthropic, "team-fixture");
    let lead = Agent::new("lead")
        .instructions("Coordinate.")
        .tool("test.probe")
        .planning(true)
        .fallback_model(ProviderKind::Openai, "team-fixture")
        .subagent(worker);
    let h = harness(&root, vec![lead]).await;
    assert_eq!(h.agents().collect::<Vec<_>>(), ["lead", "worker"]);
    assert!(
        h.session("worker", workspace(&root)).await.is_err(),
        "subagents are started through their lead"
    );
    let s = h.session("lead", workspace(&root)).await.unwrap();
    let team = s.team().expect("lead with subagents gets a team");
    let worker_sid = s.reference().team.as_ref().unwrap().workers[0]
        .session_id
        .clone();

    let item = team
        .create_item(ItemSpec::new("Produce proof", "One durable proof").description("Write once"))
        .await
        .unwrap();
    let plan = format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":"team_assign","arguments":{
            "item_id":item["id"],"expected_revision":item["revision"],"session_id":worker_sid}}])
    );
    let r = within(s.run_with(&plan, |i| {
        assert_eq!(i.agent, "worker", "the worker's tool call needs approval");
        Response::Approve(ApprovalDecision::Once)
    }))
    .await
    .unwrap();
    assert_eq!(r.status, RunStatus::Completed);
    let board = r.board.unwrap();
    assert_eq!(
        board["items"][item["id"].as_str().unwrap()]["status"],
        "done"
    );
    assert_eq!(
        std::fs::read_to_string(root.join("work/team-proof.txt")).unwrap(),
        "TEAM_PROOF\n"
    );
    h.shutdown().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn invalid_definitions_fail_before_anything_starts() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let ok = || Agent::new("a").instructions("x").tool("sdk.probe");
    let cases = [
        Agent::new("a").instructions("x"),
        Agent::new("bad id").instructions("x").tool("sdk.probe"),
        Agent::new("a").tool("sdk.probe"),
        ok().interrupt_on(["test.probe"]),
        ok().tool("unqualified"),
        ok().subagent(
            Agent::new("b")
                .instructions("x")
                .tool("sdk.probe")
                .subagent(ok()),
        ),
        ok().name("Same").subagent(
            Agent::new("b")
                .name("same")
                .instructions("x")
                .tool("sdk.probe"),
        ),
        Agent::new("a")
            .instructions("x".repeat(70_000))
            .tool("sdk.probe"),
    ];
    for agent in cases {
        let built = Harness::builder()
            .state_dir(root.join("state"))
            .executable(runner())
            .agent(agent.clone())
            .embedded()
            .await;
        assert!(
            matches!(built, Err(openworker_sdk::Error::InvalidInput(_))),
            "{agent:?} should be rejected"
        );
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn telemetry_is_opt_in_validated_and_never_blocks_runs() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let agent = || {
        Agent::new("assistant")
            .instructions("Use the probe when asked.")
            .tool("sdk.probe")
            .approvals(Approvals::Never)
    };
    // Plain HTTP to a non-loopback host is refused before anything starts.
    let refused = Harness::builder()
        .state_dir(root.join("refused"))
        .executable(runner())
        .agent(agent())
        .tools(tool::registry())
        .sandbox(Sandbox::Off)
        .scripted(true)
        .otlp("http://collector.example.com:4318")
        .embedded()
        .await;
    assert!(refused.is_err());

    // A loopback collector that is not running only costs dropped exports.
    let h = Harness::builder()
        .state_dir(root.join("state"))
        .executable(runner())
        .agent(agent())
        .tools(tool::registry())
        .sandbox(Sandbox::Off)
        .scripted(true)
        .otlp("http://127.0.0.1:9")
        .embedded()
        .await
        .unwrap();
    let s = h.session("assistant", workspace(&root)).await.unwrap();
    let r = within(s.run(&probe_plan())).await.unwrap();
    assert_eq!(r.status, RunStatus::Completed);
    within(h.shutdown()).await.unwrap();
}

/// What a TypeScript/Python SDK does: JSON agent definitions, a child
/// process, and only `agent/*` + `run/*` requests. No Rust agent types.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn raw_protocol_over_a_child_process() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = workspace(&root);
    let host: openworker_sdk::HostConfig = serde_json::from_value(json!({
        "state_dir": root.join("state"),
        "runner": runner(),
        "coworkers": [],
        "sandbox": "off",
        "scripted": true,
        "agents": [{
            "id": "assistant",
            "instructions": "Use the probe when asked.",
            "tools": ["sdk.probe", "test.probe"],
            "approvals": {"mode": "only", "tools": ["test.probe"]}
        }]
    }))
    .unwrap();
    let c = openworker_sdk::HarnessClient::managed_host(
        openworker_sdk::HostProcessConfig {
            program: runner(),
            host,
            restart: openworker_sdk::RestartPolicy::default(),
        },
        openworker_sdk::ClientOptions::default(),
    )
    .await
    .unwrap();
    assert_eq!(c.info()["capabilities"]["agent_runs"], true);
    let agents = c.request("agent/list", json!({})).await.unwrap();
    assert_eq!(agents["agents"][0]["id"], "assistant");
    let s = c
        .request(
            "agent/session",
            json!({"agent": "assistant", "workspace": work}),
        )
        .await
        .unwrap();
    let sid = s["session"]["session_id"].as_str().unwrap().to_owned();
    let started = c
        .request(
            "run/start",
            json!({"session_id": sid, "submission_id": "sub-1", "text": probe_plan()}),
        )
        .await
        .unwrap();
    let mut cursors = started["cursors"].clone();
    let mut auto = false;
    let done = within(async {
        loop {
            let v = c
                .request(
                    "run/wait",
                    json!({"session_id": sid, "submission_id": "sub-1",
                           "wait_ms": 2000, "cursors": cursors}),
                )
                .await
                .unwrap();
            for e in v["events"].as_array().unwrap() {
                auto |= e["summary"]["type"] == "auto_approved";
            }
            cursors = v["cursors"].clone();
            if v["status"] != "running" && v["more_events"] != true {
                return v;
            }
        }
    })
    .await;
    assert_eq!(done["status"], "completed");
    assert!(done["final_output"]
        .as_str()
        .unwrap()
        .contains("Resource fixture finished"));
    assert!(auto, "host approved sdk.probe under interrupt_on");
    // Re-submitting the same submission ID is a no-op, not a second turn.
    let again = c
        .request(
            "run/start",
            json!({"session_id": sid, "submission_id": "sub-1", "text": probe_plan()}),
        )
        .await
        .unwrap();
    assert_eq!(again["duplicate"], true);
    assert_eq!(
        std::fs::read_to_string(root.join("work/proof.txt")).unwrap(),
        "called\n"
    );
    c.shutdown().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn agent_defaults_and_per_turn_budget_override_reach_the_runtime() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let h = harness(
        &root,
        vec![Agent::new("limited")
            .instructions("Use the probe when asked.")
            .tool("sdk.probe")
            .approvals(Approvals::Never)
            .limits(TurnLimits {
                max_model_calls: 1,
                ..Default::default()
            })],
    )
    .await;
    let s = h.session("limited", workspace(&root)).await.unwrap();
    let first = within(s.send(probe_plan())).await.unwrap();
    let result = within(first.result()).await.unwrap();
    assert_eq!(result.status, RunStatus::Completed);
    assert_eq!(s.state().await.unwrap()["last_outcome"], "completed");
    assert!(!root.join("work/proof.txt").exists());
    let next = within(s.send_with_limits(
        &probe_plan(),
        TurnLimits {
            max_model_calls: 4,
            ..Default::default()
        },
    ))
    .await
    .unwrap();
    assert_eq!(
        within(next.result()).await.unwrap().status,
        RunStatus::Completed
    );
    assert_eq!(
        std::fs::read_to_string(root.join("work/proof.txt")).unwrap(),
        "called\n"
    );
    h.shutdown().await.unwrap();
}
