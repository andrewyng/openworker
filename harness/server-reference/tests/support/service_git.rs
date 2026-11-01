//! Optional C6 layer of service_v2. Real local OpenShell, synthetic Git remote.
use super::*;
use openworker_sdk::service::{Agent, RuntimeProfile, Service, SessionRef};
pub struct Git {
    pub host: String,
    pub profile: RuntimeProfile,
    pub write_profile: Option<RuntimeProfile>,
}
pub async fn configure(value: &mut Value) -> Option<Git> {
    let provider = std::env::var("OW_GIT_PROVIDER").ok()?;
    let host = std::env::var("OW_GIT_HOST").unwrap();
    assert!(
        provider.starts_with("owgit-c6-") && host.ends_with(".svc.cluster.local:8443"),
        "Local fixture only"
    );
    let plain: Config = serde_json::from_value(value.clone()).unwrap();
    let pin = plain
        .provider()
        .unwrap()
        .pin_credential_provider(&provider)
        .await
        .unwrap();
    value["openshell"]["git_grants"] = json!({"fixture-git@1":{"provider":pin,"scope":{"host":host,"repositories":["org/project"]}}});
    let profile: RuntimeProfile = serde_json::from_value(json!({"id":"git@1","provider":"openshell","cpu":1,"memoryMB":768,"sharing":"shared","approvalMode":"bypass","autoSkipQuestions":true,"allowedPrincipals":["alice"],"gitAccess":"fixture-git@1"})).unwrap();
    let write_profile = if let Ok(name) = std::env::var("OW_GIT_WRITE_PROVIDER") {
        assert!(name.starts_with("owgit-c6-"), "Local fixture only");
        let pin = plain
            .provider()
            .unwrap()
            .pin_credential_provider(&name)
            .await
            .unwrap();
        value["openshell"]["git_grants"]["fixture-git-write@1"] =
            json!({"provider":pin,"scope":{"host":host,"repositories":["org/project"]}});
        let mut p = profile.clone();
        p.id = "git-write@1".into();
        p.git_access = Some("fixture-git-write@1".into());
        Some(p)
    } else {
        None
    };
    Some(Git {
        host,
        profile,
        write_profile,
    })
}
pub async fn register(admin: &Service, profile: &RuntimeProfile) {
    let mut missing = profile.clone();
    missing.id = "missing-git@1".into();
    missing.git_access = Some("uninstalled@1".into());
    assert!(admin
        .register_runtime_profile(&missing, "missing-git")
        .await
        .is_err());
    let entry = admin
        .register_runtime_profile(profile, &format!("{}-profile", profile.id))
        .await
        .unwrap();
    assert_eq!(
        entry.definition["gitAccess"],
        serde_json::to_value(&profile.git_access).unwrap()
    );
    admin
        .activate_runtime_profile(&profile.id, None, &format!("activate-{}", profile.id))
        .await
        .unwrap();
}
async fn evidence(service: &Service, sid: &str, marker: &str) {
    let events = service
        .json(
            "GET",
            &format!("/v1/sessions/{sid}/events/page?after=0"),
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
                && tool_data(e)["exit_code"] == 0
                && tool_data(e)["stdout"]
                    .as_str()
                    .is_some_and(|s| s.contains(marker))),
        "Missing successful Git/denial proof: {events}"
    );
}
pub async fn qualify(
    service: &Service,
    coder: &Agent,
    g: &Git,
    path: &Path,
    worker: &mut Child,
) -> SessionRef {
    let lead = coder
        .new_session_with(
            openworker_sdk::service::SessionOptions {
                runtime_profile: Some(g.profile.id.clone()),
                project: Some("git-qualification".into()),
                ..Default::default()
            },
            Some("git-lead"),
        )
        .await
        .unwrap();
    let grants = g.profile.delegated_grants();
    let options = json!({"agentId":"lead","agentTypes":{"builder":"coder@1"},"policy":{"communication":{"board":true,"directMessages":true},"max_instances":16},"delegation":{"tools":["coding.run_shell"],"placements":["team","member","new"],"models":[],"profiles":{"git@1":grants,"small@1":{"files":[],"network":[],"credentials":[],"cpu":1,"memory_mb":512}}}});
    if let Some(write) = &g.write_profile {
        let mut bad = options.clone();
        bad["delegation"]["profiles"][&write.id] = json!(write.delegated_grants());
        let error = lead
            .create_team(serde_json::from_value(bad).unwrap())
            .await
            .err()
            .unwrap();
        assert_eq!(error.status, 403);
        // Inspect before issuing corrected options: an unknown response never
        // permits us to assume the original mutation did not commit.
        assert!(lead.snapshot().await.unwrap()["session"]["agent_team"].is_null());
    }
    let team = lead
        .create_team(serde_json::from_value(options).unwrap())
        .await
        .unwrap();
    let command=format!("git clone -q https://{}/org/project.git sdk-checkout && git -C sdk-checkout fetch -q origin && test \"$(cat sdk-checkout/proof.txt)\" = 'original source' && ! grep -Eq 'credential|openshell:resolve' sdk-checkout/.git/config && if git -C sdk-checkout push origin HEAD:refs/heads/agents/sdk-denied >/dev/null 2>&1; then exit 17; else printf 'sdk-git-read-ok'; fi",g.host);
    let run = lead
        .send(
            &plan(
                "run_shell",
                json!({"command":command,"yield_ms":10000,"timeout_seconds":40}),
            ),
            Some("git-clone"),
        )
        .await
        .unwrap();
    assert_eq!(run.result().await.unwrap().status, "completed");
    evidence(service, lead.id(), "sdk-git-read-ok").await;
    let shared=team.spawn_with_options("spawn-shared-git","builder","shared-git-reader",&plan("run_shell",json!({"command":"git -C sdk-checkout fetch -q origin && printf 'shared-git-capability-ok'","yield_ms":10000})),serde_json::from_value(json!({"sandbox":{"mode":"member","agent_id":"lead"}})).unwrap()).await.unwrap();
    completed_member(&team, "shared-git-reader").await;
    let shared_id = shared["instance"]["session_id"].as_str().unwrap();
    evidence(service, shared_id, "shared-git-capability-ok").await;
    let snapshot = service
        .json("GET", &format!("/v1/sessions/{shared_id}"), None, None)
        .await
        .unwrap();
    assert_eq!(
        snapshot["session"]["environment"]["environment_id"],
        lead.environment_id()
    );
    let define=lead.send(&plan("team_define_type",json!({"name":"credential-free","instructions":"Verify requested operations without Git credentials.","tools":["coding.run_shell"],"sandbox":{"mode":"new","profile":"small@1"}})),Some("define-credential-free")).await.unwrap();
    assert_eq!(define.result().await.unwrap().status, "completed");
    let deny=format!("test -z \"$GITHUB_TOKEN\" && test ! -d sdk-checkout && if git ls-remote https://{}/org/project.git >/dev/null 2>&1; then exit 18; else printf 'credential-free-ok'; fi",g.host);
    let plain = team
        .spawn(
            "spawn-no-git",
            "credential-free",
            "plain-worker",
            &plan("run_shell", json!({"command":deny,"yield_ms":10000})),
        )
        .await
        .unwrap();
    completed_member(&team, "plain-worker").await;
    let sid = plain["instance"]["session_id"].as_str().unwrap();
    evidence(service, sid, "credential-free-ok").await;
    let child = service
        .json("GET", &format!("/v1/sessions/{sid}"), None, None)
        .await
        .unwrap();
    assert_ne!(
        child["session"]["environment"]["environment_id"],
        lead.environment_id()
    );
    assert!(child["session"]["coworker"]
        .as_str()
        .unwrap()
        .starts_with("tt_"));
    let same=team.spawn_with_options("spawn-git-worker","builder","git-worker",&plan("run_shell",json!({"command":format!("git ls-remote https://{}/org/project.git HEAD >/dev/null && printf 'git-child-ok'",g.host),"yield_ms":10000})),serde_json::from_value(json!({"sandbox":{"mode":"new","profile":"git@1","network":grants.network,"credentials":grants.credentials,"cpu":1,"memory_mb":768}})).unwrap()).await.unwrap();
    completed_member(&team, "git-worker").await;
    evidence(
        service,
        same["instance"]["session_id"].as_str().unwrap(),
        "git-child-ok",
    )
    .await;
    // Replace only this fixture's worker; no API/controller replacement or tool
    // action retry. The next NEW turn reuses the original Git environment.
    worker.kill().await.unwrap();
    *worker = Command::new(BIN)
        .arg("--config")
        .arg(path)
        .arg("service-worker")
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let resumed = service.resume(lead.reference()).await.unwrap();
    let run=resumed.send(&plan("run_shell",json!({"command":"git -C sdk-checkout fetch -q origin && printf 'git-restarted-ok'","yield_ms":10000})),Some("git-after-restart")).await.unwrap();
    assert_eq!(run.result().await.unwrap().status, "completed");
    evidence(service, resumed.id(), "git-restarted-ok").await;
    if let Some(write) = &g.write_profile {
        let writer = coder
            .new_session_with(
                openworker_sdk::service::SessionOptions {
                    runtime_profile: Some(write.id.clone()),
                    project: Some("git-qualification".into()),
                    ..Default::default()
                },
                Some("explicit-git-writer"),
            )
            .await
            .unwrap();
        let branch = format!("refs/heads/agents/sdk-{}", writer.environment_id());
        let command=format!("git clone -q https://{}/org/project.git publish-checkout && printf 'SDK publication proof\\n' > publish-checkout/sdk-proof.txt && git -C publish-checkout add sdk-proof.txt && git -C publish-checkout -c user.name=Fixture -c user.email=f@example.invalid commit -qm sdk-proof && git -C publish-checkout push -q origin HEAD:{} && printf 'sdk-git-push-ok'",g.host,branch);
        let run = writer
            .send(
                &plan(
                    "run_shell",
                    json!({"command":command,"yield_ms":10000,"timeout_seconds":40}),
                ),
                Some("explicit-git-push"),
            )
            .await
            .unwrap();
        assert_eq!(run.result().await.unwrap().status, "completed");
        evidence(service, writer.id(), "sdk-git-push-ok").await;
        eprintln!("PASS explicit SDK write profile publication to local Git and forbidden cross-grant child delegation");
    }
    eprintln!("PASS Rust SDK Git profile, real OpenShell clone/fetch, read-only push denial, shared credential capability, same-grant isolated child, credential-free private child and worker restart");
    resumed.reference()
}
