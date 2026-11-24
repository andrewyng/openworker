#![cfg(feature = "test-support")]
use futures_util::StreamExt;
use openworker_sdk::agents::*;
use serde_json::json;

#[tokio::test]
async fn startup_definition_conflict_is_actionable_and_contains_no_prompt() {
    for managed in [false, true] {
        let root = tempfile::tempdir().unwrap();
        let executable = std::path::PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
        let builder = || {
            Harness::builder()
                .state_dir(root.path())
                .sandbox(Sandbox::Off)
                .scripted(true)
                .executable(executable.clone())
        };
        let first = Agent::new("helper")
            .instructions("PRIVATE_ORIGINAL")
            .tools([] as [&str; 0]);
        let h = builder().embedded().await.unwrap();
        h.create_agent(first).await.unwrap();
        h.shutdown().await.unwrap();
        drop(h);
        let changed = builder().agent(
            Agent::new("helper")
                .instructions("PRIVATE_NEW")
                .tools([] as [&str; 0]),
        );
        let result = if managed {
            changed
                .managed(executable.clone(), Default::default())
                .await
        } else {
            changed.embedded().await
        };
        let error = match result {
            Ok(_) => panic!("Changed definition accepted"),
            Err(e) => e,
        };
        let text = error.to_string();
        assert!(
            text.contains("helper")
                && text.contains("instructions")
                && text.contains("new agent name"),
            "{text}"
        );
        assert!(!text.contains("PRIVATE"));
    }
}

#[cfg(unix)]
#[tokio::test]
async fn checkpoint_replays_events_generated_between_response_and_reattach() {
    tokio::time::timeout(std::time::Duration::from_secs(30), checkpoint_case())
        .await
        .expect("checkpoint regression timed out");
}

#[cfg(unix)]
async fn checkpoint_case() {
    let root = tempfile::tempdir().unwrap();
    let work = root.path().join("work");
    std::fs::create_dir(&work).unwrap();
    let h = Harness::builder()
        .state_dir(root.path().join("state"))
        .executable(env!("CARGO_BIN_EXE_sdk-test-host"))
        .sandbox(Sandbox::Off)
        .scripted(true)
        .agent(
            Agent::new("helper")
                .instructions("Write the requested file.")
                .tool("coding.write_file")
                .approvals(Approvals::Always),
        )
        .embedded()
        .await
        .unwrap();
    let session = h.session("helper", &work).await.unwrap();
    let run = session
        .send(&format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([
                {"name":"coding__write_file","arguments":{"path":"result.txt","content":"done"}}
            ])
        ))
        .await
        .unwrap();
    let mut events = run.events();
    while let Some(event) = events.next().await {
        event.unwrap();
    }
    let checkpoint: RunCheckpoint =
        serde_json::from_slice(&serde_json::to_vec(&run.checkpoint()).unwrap()).unwrap();
    let waiting = run.result().await.unwrap();
    assert_eq!(waiting.status, RunStatus::Interrupted);
    session
        .respond(
            &waiting.interruptions[0],
            Response::Approve(ApprovalDecision::Once),
        )
        .await
        .unwrap();
    assert_eq!(session.resume().await.unwrap().status, RunStatus::Completed);
    let mut resumed = session.resume_streamed_from(&checkpoint).unwrap();
    let mut found = false;
    while let Some(event) = resumed.next().await {
        if matches!(event, RunEvent::ToolFinished { .. }) {
            found = true;
        }
    }
    assert!(
        found,
        "Lost tool completion between approval and stream reattachment"
    );
    assert!(resumed.checkpoint().cursors[session.id()] > checkpoint.cursors[session.id()]);
    assert_eq!(resumed.finish().await.unwrap().status, RunStatus::Completed);
    assert_eq!(
        std::fs::read_to_string(work.join("result.txt")).unwrap(),
        "done"
    );
    h.shutdown().await.unwrap();
}
