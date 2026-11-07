//! Explicit local gateway probe, before spending real model tokens. The private
//! intent/binding survives errors; never repeat creation or an uncertain launch.
use anyhow::{ensure, Context, Result};
use openworker_runtime::{
    environment::{Management, Phase, Provider, Record, Sharing},
    environment_openshell::OpenShell,
    sandbox::ExecConnectionConfig,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{fs::OpenOptions, io::Write, path::PathBuf};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    endpoint: String,
    certificates: PathBuf,
    tls_server_name: Option<String>,
    image: String,
    state: PathBuf,
}
fn save(root: &std::path::Path, name: &str, v: impl Serialize) -> Result<()> {
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(root.join(name))?;
    f.write_all(&serde_json::to_vec_pretty(&v)?)?;
    f.sync_all()?;
    Ok(())
}
async fn tool(
    c: &mut Box<dyn openworker_runtime::environment::Connection>,
    name: &str,
    args: Value,
) -> Result<Value> {
    let r = c
        .request("tools/call", json!({"name":name,"arguments":args}))
        .await?;
    ensure!(r["isError"] != true, "Tool failed: {r}");
    Ok(serde_json::from_str(
        r["content"][0]["text"]
            .as_str()
            .context("Missing tool result")?,
    )?)
}
#[tokio::main]
async fn main() -> Result<()> {
    let path = std::env::args()
        .nth(1)
        .context("Pass a private local probe config")?;
    let c: Config = serde_json::from_slice(&std::fs::read(path)?)?;
    let url = reqwest::Url::parse(&c.endpoint)?;
    ensure!(
        url.scheme() == "https" && matches!(url.host_str(), Some("127.0.0.1" | "localhost")),
        "Loopback verified TLS gateway only"
    );
    std::fs::create_dir_all(&c.state)?;
    let p = OpenShell::new(
        "openshell-k8s".into(),
        "local-process-probe".into(),
        ExecConnectionConfig {
            endpoint: c.endpoint,
            certificates: c.certificates,
            tls_server_name: c.tls_server_name,
            workspace: "default".into(),
        },
        c.image,
    )?;
    let mode = std::env::args().nth(2).unwrap_or_default();
    if mode == "--cleanup-original" {
        let record: Record = serde_json::from_slice(&std::fs::read(c.state.join("intent.json"))?)?;
        ensure!(
            record.actor == "local-qualification",
            "Only this helper's owned probe may be cleaned up"
        );
        let id = uuid::Uuid::parse_str(
            record
                .id
                .strip_prefix("env_")
                .context("Invalid environment ID")?,
        )?;
        let binding = p
            .bind_existing(&record, &format!("owr-{}", &id.simple().to_string()[..15]))
            .await?;
        if !c.state.join("binding.json").exists() {
            save(&c.state, "binding.json", &binding)?;
        }
        let operation = uuid::Uuid::new_v4().to_string();
        save(
            &c.state,
            "cleanup-intent.json",
            json!({"operation_id":operation,"binding":binding}),
        )?;
        p.delete(binding.clone(), operation).await?;
        ensure!(
            p.confirm_deleted(binding).await?,
            "Original probe deletion not confirmed"
        );
        save(
            &c.state,
            "cleanup-result.json",
            json!({"original_probe_deleted":true,"no_repeated_creation_or_tool_launch":true}),
        )?;
        println!("Original probe reconciled and deleted; no creation or tool was replayed.");
        return Ok(());
    }
    if mode == "--confirm-deleted" {
        let binding = serde_json::from_slice(&std::fs::read(c.state.join("binding.json"))?)?;
        ensure!(
            p.confirm_deleted(binding).await?,
            "Original environment still exists"
        );
        save(
            &c.state,
            "confirmed-deleted.json",
            json!({"deleted":true,"read_only_reconciliation":true}),
        )?;
        println!("Original probe environment deletion confirmed.");
        return Ok(());
    }
    let resume_process = mode == "--resume-process";
    let resume = mode == "--resume-create" || resume_process;
    let binding = if resume {
        // Read-only reconciliation of the original uncertain creation. Never
        // call create again, and never repeat a possibly delivered tool launch.
        ensure!(
            resume_process || !c.state.join("process.json").exists(),
            "Inspect the saved process handle before further actions"
        );
        let record: Record = serde_json::from_slice(&std::fs::read(c.state.join("intent.json"))?)?;
        let raw = record
            .id
            .strip_prefix("env_")
            .context("Invalid environment ID")?;
        let id = uuid::Uuid::parse_str(raw)?;
        p.bind_existing(&record, &format!("owr-{}", &id.simple().to_string()[..15]))
            .await?
    } else {
        let record = Record {
            id: openworker_runtime::id("env"),
            request_id: openworker_runtime::id("op"),
            actor: "local-qualification".into(),
            spec: p.spec(Sharing::Shared, Management::Harness),
            phase: Phase::Provisioning,
            binding: None,
            revision: 1,
        };
        save(&c.state, "intent.json", &record)?;
        p.create(record).await?
    };
    if !c.state.join("binding.json").exists() {
        save(&c.state, "binding.json", &binding)?;
    }
    let argv = || {
        vec![
            "/opt/openworker/openworker".into(),
            "mcp-coding".into(),
            "--workspace".into(),
            "/sandbox".into(),
            "--environment-processes".into(),
        ]
    };
    let mut a = p
        .attach(binding.clone(), uuid::Uuid::new_v4().to_string(), argv())
        .await?;
    a.request("initialize",json!({"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"continuity-probe","version":"1"}})).await?;
    let server = if resume_process {
        let server: Value = serde_json::from_slice(&std::fs::read(c.state.join("process.json"))?)?;
        let status = tool(
            &mut a,
            "process_status",
            json!({"process_id":server["process_id"]}),
        )
        .await?;
        let logs = tool(
            &mut a,
            "process_logs",
            json!({"process_id":server["process_id"]}),
        )
        .await?;
        save(
            &c.state,
            "reconciled-process.json",
            json!({"status":status,"logs":logs}),
        )?;
        ensure!(status["status"] == "running", "Original process is no longer running; inspect saved logs, never repeat launch automatically");
        server
    } else {
        tool(
            &mut a,
            "write_file",
            json!({"path":"probe.txt","content":"probe-ready","create_only":true}),
        )
        .await?;
        let server=tool(&mut a,"process_start",json!({"command":"node -e 'const fs=require(\"fs\"),h=require(\"http\");h.createServer((q,s)=>s.end(fs.readFileSync(\"probe.txt\",\"utf8\"))).listen(4002,\"127.0.0.1\");setInterval(()=>console.log(\"alive\"),250)'","timeout_seconds":300})).await?;
        save(&c.state, "process.json", &server)?;
        server
    };
    // A successful spawn is not HTTP readiness. Waiting here is read-only and
    // does not repeat an uncertain action or spawn a second server.
    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    let first = tool(
        &mut a,
        "run_shell",
        json!({"command":"curl --max-time 3 -sS http://127.0.0.1:4002","yield_ms":10000}),
    )
    .await?;
    ensure!(
        first["stdout"] == "probe-ready",
        "First endpoint failed: {first}"
    );
    let closed = a.close().await?;
    save(&c.state, "first-close.json", format!("{closed:?}"))?;
    let mut b = p
        .attach(binding.clone(), uuid::Uuid::new_v4().to_string(), argv())
        .await?;
    b.request("initialize",json!({"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"continuity-probe","version":"1"}})).await?;
    let status = tool(
        &mut b,
        "process_status",
        json!({"process_id":server["process_id"]}),
    )
    .await?;
    ensure!(
        status["status"] == "running",
        "Handle did not survive: {status}"
    );
    let second = tool(
        &mut b,
        "run_shell",
        json!({"command":"curl --max-time 3 -sS http://127.0.0.1:4002","yield_ms":10000}),
    )
    .await?;
    ensure!(
        second["stdout"] == "probe-ready",
        "Endpoint/file access did not survive: {second}"
    );
    tool(
        &mut b,
        "process_stop",
        json!({"process_id":server["process_id"]}),
    )
    .await?;
    b.close().await?;
    let operation = uuid::Uuid::new_v4().to_string();
    save(
        &c.state,
        "delete-intent.json",
        json!({"operation_id":operation,"binding":binding}),
    )?;
    p.delete(binding, operation).await?;
    save(
        &c.state,
        "result.json",
        json!({"passed":true,"same_handle_after_attachment_close":true,"http_file_read_after_close":true,"original_environment_deleted":true}),
    )?;
    println!("OpenShell process continuity passed; private evidence retained.");
    Ok(())
}
