//! C7: two teams, four builders, four readers, shared and independent sandboxes.
use super::*;
use openworker_sdk::service::{Agent, AgentTeam, Service, Session};
use openworker_store_postgres::Config as DbConfig;
async fn source_ready(team: &AgentTeam) {
    tokio::time::timeout(Duration::from_secs(180), async {
        loop {
            let s = team.snapshot().await.unwrap();
            assert_ne!(s["source_setup"]["status"], "blocked", "{s}");
            if s["source_setup"]["status"] == "ready" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    })
    .await
    .unwrap();
}
fn task(name: &str, args: Value) -> String {
    super::plan(&format!("coding__{name}"), args)
}
#[allow(clippy::too_many_arguments)]
pub(super) async fn qualify(
    service: &Service,
    coder: &Agent,
    first: &Session,
    first_team: &AgentTeam,
    base: &str,
    config: &Path,
    worker: &mut Child,
    db: &DbConfig,
) {
    let second = coder
        .new_session_with(Default::default(), Some("parallel-second-lead"))
        .await
        .unwrap();
    let mut o = handoff::options(base);
    o.agent_types.insert("reader".into(), "coder@1".into());
    o.coding.as_mut().unwrap().source.remote_ref = Some("refs/heads/main".into());
    let second_team = second.create_team(o).await.unwrap();
    source_ready(&second_team).await;
    assert_ne!(first.environment_id(), second.environment_id());
    // Provision all reservations while execution is stopped. After restart all
    // four initial turns compete for the same bounded four-slot scheduler.
    worker.kill().await.unwrap();
    let teams = [first_team, &second_team];
    let leads = [first, &second];
    let mut builders = vec![];
    for (index, team) in teams.iter().enumerate() {
        for (name, shared) in [("shared", true), ("isolated", false)] {
            let command=format!("test \"$(cat proof.txt)\" = base && sleep 8 && printf 'team-{index}-{name}' > build.txt && printf 'team-{index}-{name}'");
            let message = task(
                "run_shell",
                json!({"command":command,"yield_ms":10000,"timeout_seconds":30}),
            );
            let options = if shared {
                json!({"sandbox":{"mode":"team"}})
            } else {
                json!({})
            };
            let child = team
                .spawn_with_options(
                    &format!("spawn-{name}"),
                    "builder",
                    name,
                    &message,
                    serde_json::from_value(options).unwrap(),
                )
                .await
                .unwrap();
            builders.push((index, name, shared, child));
        }
    }
    tokio::time::timeout(Duration::from_secs(180), async {
        loop {
            let mut ready = 0;
            for (i, name, _, _) in &builders {
                let rows = teams[*i].members(None, 32).await.unwrap();
                let row = rows["members"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|r| r["instance"]["agent_id"] == *name)
                    .unwrap();
                assert!(row["error"].is_null(), "{row}");
                if row["instance"]["phase"] == "ready" {
                    ready += 1
                }
            }
            if ready == 4 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    })
    .await
    .unwrap();
    *worker = Command::new(BIN)
        .arg("--config")
        .arg(config)
        .arg("service-worker")
        .args(["--slots", "4"])
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut ids = vec![];
    for (index, name, shared, child) in &builders {
        completed_member(teams[*index], name).await;
        let sid = child["instance"]["session_id"].as_str().unwrap();
        ids.push(sid.to_owned());
        let s = service
            .json("GET", &format!("/v1/sessions/{sid}"), None, None)
            .await
            .unwrap();
        assert_eq!(
            s["session"]["environment"]["environment_id"] == leads[*index].environment_id(),
            *shared
        );
        assert_ne!(
            s["session"]["environment"]["environment_id"],
            leads[1 - *index].environment_id()
        );
        let path = child["workspace"]["path"].as_str().unwrap();
        let message = task("read_file", json!({"path":format!("{path}/build.txt")}));
        let reader = format!("verify-{name}");
        let result = teams[*index]
            .spawn_with_options(
                &format!("reader-{name}"),
                "reader",
                &reader,
                &message,
                serde_json::from_value(json!({"sandbox":{"mode":"member","agent_id":name}}))
                    .unwrap(),
            )
            .await
            .unwrap();
        completed_member(teams[*index], &reader).await;
        let rsid = result["instance"]["session_id"].as_str().unwrap();
        let r = service
            .json("GET", &format!("/v1/sessions/{rsid}"), None, None)
            .await
            .unwrap();
        assert_eq!(
            r["session"]["environment"]["environment_id"],
            s["session"]["environment"]["environment_id"]
        );
        let events = service
            .json(
                "GET",
                &format!("/v1/sessions/{rsid}/events/page?after=0"),
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
                    && e["data"]["name"] == "coding__read_file"
                    && tool_data(e)
                        .to_string()
                        .contains(&format!("team-{index}-{name}"))),
            "Missing exact member-sandbox file read"
        );
    }
    // Audit timestamps are database-side evidence that initial tool intervals
    // overlapped; mere concurrent HTTP submissions are not proof of execution.
    tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        let row=pg.query_one(&format!("SELECT count(DISTINCT session_id),max(created) FILTER(WHERE kind='tool_started')<min(created) FILTER(WHERE kind='tool_finished') FROM {}.audit WHERE session_id=ANY($1) AND kind IN ('tool_started','tool_finished')",db.schema),&[&ids]).unwrap();
        assert_eq!(row.get::<_, i64>(0), 4);
        assert_eq!(
            row.get::<_, Option<bool>>(1),
            Some(true),
            "All four actual tool executions must overlap"
        );
    });
    // Retiring a completed worker closes admission, not its shared filesystem.
    first_team
        .retire(
            "retire-shared",
            "shared",
            openworker_sdk::agents::RetirementMode::FinishCurrent,
        )
        .await
        .unwrap();
    assert!(first_team
        .send_message("reject-retired", "shared", "Do more work")
        .await
        .is_err());
    assert_eq!(
        first_team.members(None, 32).await.unwrap()["members"]
            .as_array()
            .unwrap()
            .len(),
        5
    );
    assert_eq!(
        second_team.members(None, 32).await.unwrap()["members"]
            .as_array()
            .unwrap()
            .len(),
        5
    );
    eprintln!("PASS C7 two teams, four overlapping builders, four member-sandbox verifiers, shared/isolated placement, worker replacement and retirement admission");
}
