//! Real embedded host/catalog, no model requests or tool execution.
use openworker_sdk::{Agent, Approvals, Error, Harness, Sandbox};

fn guide() -> Agent {
    Agent::new("guide-v1")
        .instructions("PRIVATE_ORIGINAL_PROMPT")
        .tools([] as [&str; 0])
        .planning(false)
        .memory(false)
        .scheduling(false)
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn definition_conflicts_survive_catalog_restart_without_exposing_values() {
    let root = tempfile::tempdir().unwrap();
    for pass in 0..2 {
        let h = Harness::builder()
            .state_dir(root.path().canonicalize().unwrap())
            .executable(std::path::PathBuf::from(env!(
                "CARGO_BIN_EXE_openworker-harness"
            )))
            .sandbox(Sandbox::Off)
            .start()
            .await
            .unwrap();
        h.create_agent(guide()).await.unwrap();
        h.create_agent(guide()).await.unwrap();
        let error = h
            .create_agent(
                guide()
                    .instructions("PRIVATE_NEW_PROMPT")
                    .approvals(Approvals::Never),
            )
            .await
            .err()
            .expect("immutable definition must reject changes");
        let Error::AgentDefinitionConflict(diagnostic) = &error else {
            panic!("Expected structured conflict on pass {pass}, got {error:?}");
        };
        assert_eq!(diagnostic.agent_id, "guide-v1");
        assert_eq!(diagnostic.conflicting_fields, ["instructions", "approvals"]);
        assert!(!format!("{error:?} {error}").contains("PRIVATE_"));
        h.create_agent(guide()).await.unwrap();
        h.shutdown().await.unwrap();
    }
}
