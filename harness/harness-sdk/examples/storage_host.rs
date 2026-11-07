//! Compile/run without provider calls; validates an SDK consumer's host setup.
use openworker_sdk::{
    ClientOptions, HarnessClient, HostConfig, HostProcessConfig, RestartPolicy, ToolRegistry,
};
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().collect::<Vec<_>>();
    if args.len() < 3 || !["embedded", "stdio"].contains(&args[1].as_str()) {
        return Err(
            "usage: storage_host <embedded|stdio> <absolute-config.json> [--migrate]".into(),
        );
    }
    let config: HostConfig = serde_json::from_slice(&std::fs::read(&args[2])?)?;
    if args.get(3).map(String::as_str) == Some("--migrate") {
        config.storage.migrate()?;
    }
    let client = if args[1] == "embedded" {
        HarnessClient::embedded(config, ToolRegistry::default(), ClientOptions::default()).await?
    } else {
        HarnessClient::managed_stdio(
            HostProcessConfig {
                program: config.runner.clone(),
                host: config,
                restart: RestartPolicy::default(),
            },
            ClientOptions::default(),
        )
        .await?
    };
    println!("{}", client.info());
    client.shutdown().await?;
    Ok(())
}
