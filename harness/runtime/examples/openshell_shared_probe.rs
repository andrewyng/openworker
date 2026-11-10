//! Qualification only: controlled file effects inside the explicitly selected sandbox.
#[cfg(not(windows))]
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    use openworker_runtime::sandbox::{ExecConnectionConfig, Mcp};
    use serde_json::{json, Value};
    use std::time::Duration;
    async fn shell(m: &mut Mcp, command: String) -> anyhow::Result<Value> {
        let result = m
            .request(
                "tools/call",
                json!({"name":"run_shell","arguments":{"command":command}}),
            )
            .await?;
        anyhow::ensure!(result["isError"] != true, "Probe tool error");
        Ok(serde_json::from_str(
            result["content"][0]["text"]
                .as_str()
                .ok_or_else(|| anyhow::anyhow!("Missing structured tool result"))?,
        )?)
    }
    let config = ExecConnectionConfig {
        endpoint: std::env::var("OW_PROBE_ENDPOINT")?,
        certificates: std::env::var("OW_PROBE_CERTS")?.into(),
        workspace: "default".into(),
        tls_server_name: std::env::var("OW_PROBE_TLS_NAME").ok(),
    };
    let name = std::env::var("OW_PROBE_SANDBOX")?;
    let argv = vec![
        "/opt/openworker/openworker".into(),
        "mcp-example".into(),
        "--workspace".into(),
        "/sandbox".into(),
    ];
    let id = uuid::Uuid::new_v4().to_string();
    let mut a = Mcp::connect_attachment(&config, &name, argv.clone(), &id).await?;
    let mut b = Mcp::connect_v1(&config, &name, argv.clone()).await?;
    println!("PASS two independent MCP attachments opened");
    // A repeated launch ID must not create another server/process or replay stdin.
    let duplicate = Mcp::connect_attachment(&config, &name, argv.clone(), &id)
        .await
        .err()
        .ok_or_else(|| anyhow::anyhow!("Duplicate launch unexpectedly succeeded"))?;
    let status = duplicate.downcast_ref::<tonic::Status>().ok_or_else(|| {
        anyhow::anyhow!("Expected gateway admission rejection, not a transport failure")
    })?;
    anyhow::ensure!(
        status.code() == tonic::Code::FailedPrecondition
            && status.message().contains("request is admitted"),
        "Unexpected duplicate admission response"
    );
    println!("PASS repeated launch ID did not open another attachment");
    let prefix = format!("stream-{}", uuid::Uuid::new_v4().simple());
    let ready = format!("/sandbox/{prefix}-ready");
    let done = format!("/sandbox/{prefix}-done");
    let command = format!("printf started > {ready}; sleep 8; printf finished > {done}");
    let task = tokio::spawn(async move { shell(&mut a, command).await });
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            if shell(&mut b, format!("test -f {ready} && printf ready")).await?["stdout"] == "ready"
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        anyhow::Ok(())
    })
    .await??;
    task.abort();
    let _ = task.await;
    anyhow::ensure!(
        shell(&mut b, "printf second-attachment-alive".into()).await?["stdout"]
            == "second-attachment-alive"
    );
    println!("PASS dropping one in-flight attachment leaves the other usable");
    tokio::time::sleep(Duration::from_secs(10)).await;
    let observed = shell(
        &mut b,
        format!("if test -f {done}; then printf present; else printf absent; fi"),
    )
    .await?;
    // Absence is one observation, not a universal guarantee that remote effects stop.
    println!(
        "OBSERVED delayed effect after stream drop: {}",
        observed["stdout"]
    );
    let mut c = Mcp::connect_v1(&config, &name, argv).await?;
    anyhow::ensure!(tokio::time::timeout(
        Duration::from_millis(500),
        shell(&mut c, "sleep 2; printf late".into())
    )
    .await
    .is_err());
    let poison=c.request("tools/call",json!({"name":"write_file","arguments":{"path":format!("{prefix}-forbidden"),"content":"must-not-write"}})).await.unwrap_err();
    anyhow::ensure!(poison.to_string().contains("request not dispatched"));
    anyhow::ensure!(
        shell(&mut b, format!("test ! -f /sandbox/{prefix}-forbidden")).await?["exit_code"] == 0
    );
    let _ = c.close().await;
    println!("PASS cancelled request poisons its connection and prevents another dispatch");
    let boundary=shell(&mut b,"id -u; grep -E 'NoNewPrivs:|Seccomp:' /proc/self/status; cat /opt/probe-denied/sentinel; test ! -S /var/run/docker.sock && test ! -f /var/run/secrets/kubernetes.io/serviceaccount/token".into()).await?;
    let output = boundary["stdout"].as_str().unwrap_or("");
    anyhow::ensure!(
        output.lines().next().is_some_and(|s| s.trim() != "0"),
        "Sandbox UID unexpectedly root"
    );
    let fields = output.split_whitespace().collect::<Vec<_>>();
    anyhow::ensure!(
        fields.windows(2).any(|w| w == ["NoNewPrivs:", "1"])
            && fields.windows(2).any(|w| w == ["Seccomp:", "2"]),
        "Kernel enforcement flags missing"
    );
    anyhow::ensure!(!boundary["stdout"]
        .as_str()
        .unwrap_or("")
        .contains("not-a-secret-boundary-probe"));
    anyhow::ensure!(
        boundary["stderr"]
            .as_str()
            .unwrap_or("")
            .contains("Permission denied"),
        "Landlock denial not observed"
    );
    anyhow::ensure!(
        boundary["exit_code"] == 0,
        "Unexpected token/socket exposure"
    );
    println!("PASS filesystem denial, non-root, no-new-privileges, seccomp, no Docker socket or Kubernetes token");
    match b.close().await {
        Ok(()) => println!("OBSERVED idle attachment close: process exit received"),
        Err(_) => {
            println!("OBSERVED idle attachment close: no bounded exit evidence; outcome unknown")
        }
    }
    Ok(())
}
#[cfg(windows)]
fn main() {
    eprintln!("OpenShell qualification requires Linux or macOS");
}
