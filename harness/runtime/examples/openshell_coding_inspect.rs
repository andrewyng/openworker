//! Independent local acceptance of a saved service binding. No provisioning,
//! model requests, launch replay, credential reads or policy changes.
use anyhow::{ensure, Context, Result};
use openworker_runtime::{
    environment::{Binding, Provider},
    environment_openshell::OpenShell,
    sandbox::ExecConnectionConfig,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{fs::OpenOptions, io::Write, path::PathBuf};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    endpoint: String,
    certificates: PathBuf,
    tls_server_name: String,
    binding: PathBuf,
    output: PathBuf,
}
async fn call(
    c: &mut Box<dyn openworker_runtime::environment::Connection>,
    name: &str,
    args: Value,
) -> Result<Value> {
    let r = c
        .request("tools/call", json!({"name":name,"arguments":args}))
        .await?;
    ensure!(r["isError"] != true, "Inspection tool failed");
    Ok(serde_json::from_str(
        r["content"][0]["text"]
            .as_str()
            .context("Missing tool data")?,
    )?)
}
#[tokio::main]
async fn main() -> Result<()> {
    let cfg: Config = serde_json::from_slice(&std::fs::read(
        std::env::args()
            .nth(1)
            .context("Private inspect config required")?,
    )?)?;
    let u = reqwest::Url::parse(&cfg.endpoint)?;
    ensure!(
        u.scheme() == "https" && matches!(u.host_str(), Some("127.0.0.1" | "localhost")),
        "Verified loopback gateway only"
    );
    ensure!(!cfg.output.exists(), "Inspection receipt already exists");
    let binding: Binding = serde_json::from_slice(&std::fs::read(cfg.binding)?)?;
    let p = OpenShell::new(
        binding.spec.provider.clone(),
        binding.spec.provider_identity.clone(),
        ExecConnectionConfig {
            endpoint: cfg.endpoint,
            certificates: cfg.certificates,
            tls_server_name: Some(cfg.tls_server_name),
            workspace: binding.workspace_scope.clone(),
        },
        binding.spec.image.clone(),
    )?;
    p.inspect(binding.clone()).await?;
    let mut c = p
        .attach(
            binding.clone(),
            uuid::Uuid::new_v4().to_string(),
            vec![
                "/opt/openworker/openworker".into(),
                "mcp-coding".into(),
                "--workspace".into(),
                "/sandbox".into(),
                "--environment-processes".into(),
            ],
        )
        .await?;
    c.request("initialize",json!({"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"independent-acceptance","version":"1"}})).await?;
    let preview=call(&mut c,"run_shell",json!({"command":"node -e 'console.log(require(\"fs\").readFileSync(\"preview.json\",\"utf8\"))'","yield_ms":10000})).await?;
    ensure!(
        preview["exit_code"] == 0,
        "Saved preview metadata unavailable"
    );
    let preview: Value = serde_json::from_str(
        preview["stdout"]
            .as_str()
            .context("Missing preview output")?,
    )?;
    let handle = preview
        .get("process_id")
        .or_else(|| preview.get("processId"))
        .context("No saved process handle")?;
    let status = call(&mut c, "process_status", json!({"process_id":handle})).await?;
    ensure!(
        status["status"] == "running",
        "Original server is not running"
    );
    let health = call(
        &mut c,
        "run_shell",
        json!({"command":"curl --max-time 5 -fsS http://127.0.0.1:4000/health","yield_ms":10000}),
    )
    .await?;
    ensure!(health["exit_code"] == 0, "Saved server health check failed");
    let tests = call(
        &mut c,
        "run_shell",
        json!({"command":"node --test tests.mjs","yield_ms":10000}),
    )
    .await?;
    ensure!(
        tests["exit_code"] == 0,
        "Independent generated test rerun failed"
    );
    let floor=call(&mut c,"run_shell",json!({"command":"node -e 'const fs=require(\"fs\");let result;try{fs.readFileSync(\"/opt/probe-denied/sentinel\");result=\"unexpected_read\"}catch(e){result=e.code};console.log(JSON.stringify({denied:result,budgetProofExists:fs.existsSync(\"budget-proof.txt\"),modelKeyPresent:[\"OPENAI_API_KEY\",\"ANTHROPIC_API_KEY\"].some(k=>!!process.env[k])}))'","yield_ms":10000})).await?;
    let check: Value =
        serde_json::from_str(floor["stdout"].as_str().context("Missing floor result")?)?;
    ensure!(
        matches!(check["denied"].as_str(), Some("EACCES" | "EPERM"))
            && check["modelKeyPresent"] == false
            && check["budgetProofExists"] == false,
        "Sandbox floor/key boundary failed"
    );
    c.close().await?;
    let result = json!({"environment_id":binding.environment_id,"process_id":handle,"status":status,"health":health,"tests":tests,"sandbox_floor":check,"independent":true});
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(cfg.output)?;
    f.write_all(&serde_json::to_vec_pretty(&result)?)?;
    f.sync_all()?;
    println!("Independent sandbox tests, original server and kernel read-denial checks passed.");
    Ok(())
}
