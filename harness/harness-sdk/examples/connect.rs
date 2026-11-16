//! No-model smoke example. Native partner tools must be linked/registered by the host.
use openworker_sdk::*;
use serde_json::json;
use std::path::PathBuf;

#[tokio::main]
async fn main() -> std::result::Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if !(5..=6).contains(&args.len())
        || !matches!(args[0].as_str(), "embedded" | "stdio")
        || (args.len() == 6 && args[5] != "--sandbox-off")
    {
        return Err("Usage: connect <embedded|stdio> <absolute-state> <absolute-bundle> <absolute-runner> <absolute-workspace> [--sandbox-off]".into());
    }
    let mut config = RuntimeConfig::new(
        PathBuf::from(&args[1]),
        PathBuf::from(&args[2]),
        PathBuf::from(&args[3]),
    );
    if args.len() == 6 {
        config.sandbox = Sandbox::Off;
        eprintln!("Sandbox OFF: tools have host-account access.");
    }
    let client = if args[0] == "embedded" {
        HarnessClient::embedded(config, ToolRegistry::default(), ClientOptions::default()).await?
    } else {
        HarnessClient::managed_stdio(
            ProcessConfig {
                program: config.runner.clone(),
                runtime: config,
                restart: RestartPolicy::default(),
            },
            ClientOptions::default(),
        )
        .await?
    };
    let work = async {
        let list = client.request("coworker/list", json!({})).await?;
        let coworker = list["coworkers"][0]["id"]
            .as_str()
            .ok_or_else(|| Error::Remote("No coworker available".into()))?
            .to_owned();
        let _events = client.subscribe();
        let sid = client
            .create_session(RuntimeSessionOptions {
                name: "SDK connection example".into(),
                coworker,
                workspace: PathBuf::from(&args[4]),
                model: None,
                permissions: None,
            })
            .await?;
        let snap = client.attach(&sid, 0).await?;
        println!(
            "Connected; session {sid}; journal cursor {}. No model called.",
            snap["cursor"]
        );
        Ok::<_, Error>(())
    }
    .await;
    client.shutdown().await?;
    work?;
    Ok(())
}
