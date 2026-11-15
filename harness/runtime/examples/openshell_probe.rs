//! Live qualification tool. No model calls and no hidden automatic retry.
#[cfg(not(windows))]
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    use openworker_runtime::sandbox::{ExecConnectionConfig, Mcp};
    use serde_json::json;
    let config = ExecConnectionConfig {
        endpoint: std::env::var("OW_PROBE_ENDPOINT")?,
        certificates: std::env::var("OW_PROBE_CERTS")?.into(),
        workspace: "default".into(),
        tls_server_name: std::env::var("OW_PROBE_TLS_NAME").ok(),
    };
    let name = std::env::var("OW_PROBE_SANDBOX")?;
    let mut runner = Mcp::connect_v1(
        &config,
        &name,
        vec![
            "/opt/openworker/openworker".into(),
            "mcp-example".into(),
            "--workspace".into(),
            "/sandbox".into(),
        ],
    )
    .await?;
    let tools = runner.request("tools/list", json!({})).await?;
    anyhow::ensure!(tools["tools"].as_array().is_some_and(|t| t.len() >= 3));
    let result = runner.request("tools/call", json!({"name":"write_file", "arguments":{"path":"qualification.txt","content":"native-rust-through-openshell"}})).await?;
    anyhow::ensure!(result["isError"] != true, "Write failed");
    let result = runner
        .request(
            "tools/call",
            json!({"name":"read_file", "arguments":{"path":"qualification.txt"}}),
        )
        .await?;
    anyhow::ensure!(
        result.to_string().contains("native-rust-through-openshell"),
        "Readback mismatch"
    );
    println!("PASS MCP initialize/catalog/write/read over OpenShell v0.1.3 TLS exec");
    Ok(())
}
#[cfg(windows)]
fn main() {
    eprintln!("This qualification example requires Linux or macOS");
}
