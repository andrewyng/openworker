use fixture_sdk::{agents::*, Deployment};
use std::path::PathBuf;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let package = PathBuf::from(&args[1]);
    let root = PathBuf::from(&args[2]);
    for (name, mode) in [
        ("embedded", Deployment::Embedded),
        ("subprocess", Deployment::Subprocess),
    ] {
        let workspace = root.join(format!("work-{name}"));
        std::fs::create_dir_all(&workspace)?;
        let mut config = fixture_sdk::config(root.join(format!("state-{name}")), &package);
        config.sandbox = Sandbox::Off;
        config.scripted = true;
        config.agents = vec![Agent::new("assistant")
            .instructions("Use the test tool.")
            .tool("partner.note")];
        let harness = fixture_sdk::start(config, mode).await?;
        let session = harness.session("assistant", &workspace).await?;
        let result = session
            .run("TEST_RESOURCE_GRANTS [{\"name\":\"partner__note\",\"arguments\":{}}]")
            .await?;
        assert_eq!(result.status, RunStatus::Interrupted);
        assert!(!workspace.join("partner-proof.txt").exists());
        session
            .respond(
                &result.interruptions[0],
                Response::Approve(ApprovalDecision::Once),
            )
            .await?;
        assert_eq!(session.resume().await?.status, RunStatus::Completed);
        assert_eq!(
            std::fs::read_to_string(workspace.join("partner-proof.txt"))?,
            "native partner tool\n"
        );
        harness.shutdown().await?;
        println!("verified {name}");
    }
    Ok(())
}
