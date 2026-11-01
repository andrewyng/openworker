//! Physical C4 submission through API/worker, controller, artifacts and OpenShell.
use super::*;
use openworker_sdk::service::{AgentTeam, Service, Session};

pub(super) async fn qualify(
    service: &Service,
    lead: &Session,
    team: &AgentTeam,
    builder: &Value,
    base: &str,
    source_directory: &str,
    coding_prefix: &str,
) -> Value {
    let alias = |name: &str| {
        if [
            "run_shell",
            "read_file",
            "write_file",
            "git_status",
            "git_log",
        ]
        .contains(&name)
        {
            format!("{coding_prefix}{name}")
        } else {
            name.to_owned()
        }
    };
    let plan = |name: &str, args: Value| super::plan(&alias(name), args);
    let sid = builder["instance"]["session_id"].as_str().unwrap();
    let selected = service
        .json("GET", &format!("/v1/sessions/{sid}"), None, None)
        .await
        .unwrap();
    let mut reference = lead.reference();
    reference.session_id = sid.into();
    reference.environment_id = selected["session"]["environment"]["environment_id"]
        .as_str()
        .unwrap()
        .into();
    let child = service.resume(reference).await.unwrap();
    let read = child
        .send(
            &plan(
                "run_shell",
                json!({"command":"git rev-parse HEAD","yield_ms":10000}),
            ),
            Some("read-submitted-head"),
        )
        .await
        .unwrap();
    assert_eq!(read.result().await.unwrap().status, "completed");
    let events = service
        .json(
            "GET",
            &format!("/v1/sessions/{sid}/events/page?after=0"),
            None,
            None,
        )
        .await
        .unwrap();
    let commit = events["events"]
        .as_array()
        .unwrap()
        .iter()
        .rev()
        .find(|e| e["type"] == "tool_finished" && e["data"]["name"] == alias("run_shell"))
        .map(tool_data)
        .unwrap()["stdout"]
        .as_str()
        .unwrap()
        .trim()
        .to_owned();
    assert_eq!(commit.len(), 40);
    let submit = child
        .send(
            &plan(
                "team_submit_code",
                json!({"commit":commit,"summary":"Independent checkout-a change"}),
            ),
            Some("submit-committed-change"),
        )
        .await
        .unwrap();
    assert_eq!(submit.result().await.unwrap().status, "completed");
    let row = tokio::time::timeout(Duration::from_secs(180), async {
        loop {
            let catalog = team.submissions(None, 20).await.unwrap();
            if let Some(row) = catalog
                .as_array()
                .unwrap()
                .iter()
                .find(|r| r["commit"] == commit)
            {
                assert!(row["error"].is_null(), "Submission blocked: {row}");
                if row["status"] == "ready" {
                    break row.clone();
                }
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    })
    .await
    .unwrap();
    let id = row["id"].as_str().unwrap();
    let repeat = child
        .send(
            &plan(
                "team_submit_code",
                json!({"commit":commit,"summary":"Retry with new call ID"}),
            ),
            Some("repeat-submission"),
        )
        .await
        .unwrap();
    assert_eq!(repeat.result().await.unwrap().status, "completed");
    assert_eq!(
        team.submissions(None, 20)
            .await
            .unwrap()
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let task = format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([
            {"name":alias("run_shell"),"arguments":{"command":format!("test \"$(git rev-parse HEAD)\" = '{commit}' && test \"$(cat proof.txt)\" = checkout-a && test \"$(git rev-parse --absolute-git-dir)\" = \"$PWD/.git\" && printf 'verified submitted checkout'"),"yield_ms":10000}},
            {"name":"team_report_verification","arguments":{"submission_id":id,"report":"Checked exact submitted commit, proof contents and independent Git metadata."}}
        ])
    );
    let verifier = team
        .spawn_with_options(
            "spawn-submission-verifier",
            "builder",
            "submitted-verifier",
            &task,
            serde_json::from_value(json!({"source":{"kind":"submission","id":id}})).unwrap(),
        )
        .await
        .unwrap();
    completed_member(team, "submitted-verifier").await;
    let vsid = verifier["instance"]["session_id"].as_str().unwrap();
    let verification = service
        .json(
            "GET",
            &format!("/v1/sessions/{vsid}/events/page?after=0"),
            None,
            None,
        )
        .await
        .unwrap();
    assert!(
        verification["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["type"] == "tool_finished"
                && e["data"]["name"] == alias("run_shell")
                && tool_data(e)["exit_code"] == 0
                && tool_data(e)["stdout"] == "verified submitted checkout"),
        "{verification}"
    );
    let final_catalog = team.submissions(None, 20).await.unwrap();
    let row = &final_catalog[0];
    assert_eq!(row["id"], id);
    assert_eq!(row["commit"], commit);
    assert_eq!(row["reports"].as_array().unwrap().len(), 1);
    assert_eq!(
        row["reports"][0]["reporter"]["agent_id"],
        "submitted-verifier"
    );
    assert_eq!(row["reports"][0]["submitted_commit"], commit);
    let final_session = service
        .json("GET", &format!("/v1/sessions/{vsid}"), None, None)
        .await
        .unwrap();
    assert_ne!(
        selected["session"]["environment"]["remote_id"],
        final_session["session"]["environment"]["remote_id"]
    );
    let receive = lead
        .send(
            &plan("team_receive_submission", json!({"submission_id":id})),
            Some("receive-submitted-code"),
        )
        .await
        .unwrap();
    assert_eq!(receive.result().await.unwrap().status, "completed");
    let reception = tokio::time::timeout(Duration::from_secs(180), async {
        loop {
            let rows = team.submissions(None, 20).await.unwrap();
            let r = &rows[0]["reception"];
            assert!(r["error"].is_null(), "{r}");
            if r["status"] == "ready" {
                break r.clone();
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    })
    .await
    .unwrap();
    let path = reception["path"]
        .as_str()
        .unwrap()
        .strip_prefix("/sandbox/")
        .unwrap();
    let inspect = lead
        .send(
            &plan("read_file", json!({"path":format!("{path}/proof.txt")})),
            Some("inspect-received-source"),
        )
        .await
        .unwrap();
    assert_eq!(inspect.result().await.unwrap().status, "completed");
    // A rejected expected-head attempt is terminal and does not change source.
    let stale = lead
        .send(
            &plan(
                "team_integrate_submission",
                json!({"submission_id":id,"expected_head":"f".repeat(40)}),
            ),
            Some("reject-stale-base"),
        )
        .await
        .unwrap();
    assert_eq!(stale.result().await.unwrap().status, "completed");
    tokio::time::timeout(Duration::from_secs(180), async {
        loop {
            let rows = team.submissions(None, 20).await.unwrap();
            let actions = rows[0]["integrations"].as_array().unwrap();
            assert!(actions.iter().all(|a| a["error"].is_null()), "{rows}");
            if actions.iter().any(|a| a["status"] == "rejected") {
                break;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    })
    .await
    .unwrap();
    let untouched=lead.send(&plan("run_shell",json!({"command":format!("test \"$(git -C {source_directory} rev-parse HEAD)\" = '{base}' && test \"$(cat {source_directory}/proof.txt)\" = base && printf 'original lead source unchanged'"),"yield_ms":10000})),Some("inspect-stale-rejection")).await.unwrap();
    assert_eq!(untouched.result().await.unwrap().status, "completed");
    let untouched_events = service
        .json(
            "GET",
            &format!("/v1/sessions/{}/events/page?after=0", lead.id()),
            None,
            None,
        )
        .await
        .unwrap();
    assert!(
        untouched_events["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["type"] == "tool_finished"
                && tool_data(e)["exit_code"] == 0
                && tool_data(e)["stdout"] == "original lead source unchanged"),
        "Source changed before integration"
    );
    let integrate = lead
        .send(
            &plan(
                "team_integrate_submission",
                json!({"submission_id":id,"expected_head":base}),
            ),
            Some("integrate-submitted-code"),
        )
        .await
        .unwrap();
    assert_eq!(integrate.result().await.unwrap().status, "completed");
    tokio::time::timeout(Duration::from_secs(180), async {
        loop {
            let rows = team.submissions(None, 20).await.unwrap();
            let actions = rows[0]["integrations"].as_array().unwrap();
            assert!(actions.iter().all(|a| a["error"].is_null()), "{rows}");
            let current = actions.iter().find(|a| a["expected_head"] == base);
            if let Some(action) = current {
                assert_ne!(
                    action["status"], "rejected",
                    "Expected-head integration rejected: {action}"
                );
                if action["status"] == "applied" {
                    break;
                }
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    })
    .await
    .unwrap();
    let check=lead.send(&plan("run_shell",json!({"command":format!("test \"$(git -C {source_directory} rev-parse HEAD)\" = '{commit}' && test \"$(cat {source_directory}/proof.txt)\" = checkout-a && printf 'exact submitted code integrated'"),"yield_ms":10000})),Some("inspect-integrated-code")).await.unwrap();
    assert_eq!(check.result().await.unwrap().status, "completed");
    let events = service
        .json(
            "GET",
            &format!("/v1/sessions/{}/events/page?after=0", lead.id()),
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
            .any(|e| e["type"] == "tool_finished"
                && tool_data(e)["stdout"] == "original lead source unchanged"
                && tool_data(e)["exit_code"] == 0),
        "{events}"
    );
    assert!(
        events["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["type"] == "tool_finished"
                && tool_data(e)["stdout"] == "exact submitted code integrated"
                && tool_data(e)["exit_code"] == 0),
        "{events}"
    );
    eprintln!("PASS C4 independent lead reception, explicit stale-base rejection without source changes, expected-head CAS integration and actual source read-back");
    eprintln!("PASS C4 SDK submission catalog, real builder bundle export/publication, deduplicated new-call retry, distinct OpenShell verifier checkout and exact-revision attributed report");
    json!({"reference":lead.reference(),"submissionId":id,"commit":commit})
}

pub(super) fn options(base: &str) -> openworker_sdk::service::AgentTeamOptions {
    serde_json::from_value(json!({"agentId":"lead","agentTypes":{"builder":"coder@1"},"policy":{"communication":{"board":true,"directMessages":false},"max_instances":32},"limits":{"maxModelCalls":256,"maxOutputTokens":2000000,"summaryTokens":512},"delegation":{"tools":["coding.read_file"],"models":[],"placements":["team","member","new"],"profiles":{"small@1":{"files":[],"network":[],"credentials":[],"cpu":1,"memory_mb":512}}},"coding":{"source":{"repository":"org/game","directory":"source","object_format":"sha1","commit":base},"agent_types":{"builder":{"sandbox":{"mode":"new","profile":"small@1","files":[],"network":[],"credentials":[],"cpu":1,"memory_mb":512},"source":"initial"}}}})).unwrap()
}
/// Focused C4 debugging still uses the real service, SDK, workers, artifact store
/// and OpenShell; it deliberately omits the separate C1–C3 qualification cases.
pub(super) async fn prepare(
    service: &Service,
    coder: &openworker_sdk::service::Agent,
    environment: &str,
) -> Value {
    let seeder = coder
        .new_session_with(
            openworker_sdk::service::SessionOptions {
                environment_id: Some(environment.into()),
                project: Some("game".into()),
                ..Default::default()
            },
            Some("handoff-focused-seed"),
        )
        .await
        .unwrap();
    let init=seeder.send(&plan("run_shell",json!({"command":"mkdir source && cd source && git init -q && printf 'base\\n' > proof.txt && git add proof.txt && git -c user.name=Fixture -c user.email=f@example.invalid commit -qm base && git rev-parse HEAD","yield_ms":10000})),Some("initialize-handoff-source")).await.unwrap();
    assert_eq!(init.result().await.unwrap().status, "completed");
    let events = service
        .json(
            "GET",
            &format!("/v1/sessions/{}/events/page?after=0", seeder.id()),
            None,
            None,
        )
        .await
        .unwrap();
    let result = events["events"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["type"] == "tool_finished")
        .map(tool_data)
        .unwrap();
    assert_eq!(result["exit_code"], 0, "{result}");
    let base = result["stdout"].as_str().unwrap().trim();
    assert_eq!(base.len(), 40);
    let lead = coder
        .new_session_with(
            openworker_sdk::service::SessionOptions {
                environment_id: Some(environment.into()),
                project: Some("game".into()),
                ..Default::default()
            },
            Some("handoff-focused-lead"),
        )
        .await
        .unwrap();
    let team = lead.create_team(options(base)).await.unwrap();
    let task = plan(
        "run_shell",
        json!({"command":format!("test \"$(git rev-parse HEAD)\" = '{base}' && printf 'checkout-a\\n' > proof.txt && git add proof.txt && git -c user.name=Fixture -c user.email=f@example.invalid commit -qm builder"),"yield_ms":10000}),
    );
    let builder = team
        .spawn("handoff-focused-builder", "builder", "checkout-a", &task)
        .await
        .unwrap();
    completed_member(&team, "checkout-a").await;
    qualify(service, &lead, &team, &builder, base, "source", "").await
}
