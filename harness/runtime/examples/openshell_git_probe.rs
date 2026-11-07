//! Live local OpenShell/Git qualification, with fixture credentials only.
//! No model calls, external GitHub writes, host mounts or create replay.
#[cfg(not(windows))]
mod probe {
    use anyhow::{ensure, Context, Result};
    use openworker_runtime::{
        environment::{Binding, Connection, Management, Phase, Provider, Record, Sharing},
        environment_openshell::OpenShell,
        sandbox::ExecConnectionConfig,
    };
    use serde::Deserialize;
    use serde_json::{json, Value};
    use std::{
        path::{Path, PathBuf},
        time::Duration,
    };
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Config {
        endpoint: String,
        certificates: PathBuf,
        tls_server_name: String,
        image: String,
        host: String,
        provider: String,
        publication: bool,
        state: PathBuf,
        /// Optional fixture barrier lets the host orchestrator change credentials
        /// while the SAME original MCP connection/process manager is retained.
        #[serde(default)]
        barrier: bool,
        #[serde(default)]
        retain_failed: bool,
    }
    fn save(path: &Path, value: &impl serde::Serialize) -> Result<()> {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path)?;
        f.write_all(&serde_json::to_vec_pretty(value)?)?;
        f.sync_all()?;
        Ok(())
    }
    async fn connection(p: &OpenShell, b: &Binding, host: &str) -> Result<Box<dyn Connection>> {
        let mut c = p
            .attach(
                b.clone(),
                uuid::Uuid::new_v4().to_string(),
                vec![
                    "/opt/openworker/openworker".into(),
                    "mcp-coding".into(),
                    "--workspace".into(),
                    "/sandbox".into(),
                    "--environment-processes".into(),
                    "--openshell-git".into(),
                    "--openshell-git-host".into(),
                    host.into(),
                    "--openshell-git-repository".into(),
                    "org/project".into(),
                ],
            )
            .await?;
        c.request("initialize",json!({"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"openshell-git-qualification","version":"1"}})).await?;
        Ok(c)
    }
    async fn shell(c: &mut Box<dyn Connection>, command: &str) -> Result<Value> {
        let result=c.request("tools/call",json!({"name":"run_shell","arguments":{"command":command,"timeout_seconds":40,"yield_ms":10000}})).await?;
        ensure!(
            result["isError"] != true,
            "Fixture command rejected before execution"
        );
        let mut v: Value = serde_json::from_str(
            result["content"][0]["text"]
                .as_str()
                .context("Missing process result")?,
        )?;
        let deadline = tokio::time::Instant::now() + Duration::from_secs(45);
        while v["exit_code"].is_null() && v["status"] == "running" {
            ensure!(
                tokio::time::Instant::now() < deadline,
                "Fixture process outcome still unknown; no replay"
            );
            tokio::time::sleep(Duration::from_millis(200)).await;
            let result = c
                .request(
                    "tools/call",
                    json!({"name":"process_status","arguments":{"process_id":v["process_id"]}}),
                )
                .await?;
            v = serde_json::from_str(
                result["content"][0]["text"]
                    .as_str()
                    .context("Missing process status")?,
            )?;
        }
        // Do not print process output: keep credentials/references out of reports.
        Ok(v)
    }
    async fn barrier(cfg: &Config, name: &str) -> Result<()> {
        save(
            &cfg.state.join(format!("{name}.ready")),
            &json!({"ready":true}),
        )?;
        tokio::time::timeout(Duration::from_secs(180), async {
            while !cfg.state.join(format!("{name}.continue")).exists() {
                tokio::time::sleep(Duration::from_millis(200)).await
            }
        })
        .await
        .context("Fixture barrier timed out; no operation replay")?;
        Ok(())
    }
    pub async fn run() -> Result<()> {
        let cfg: Config = serde_json::from_slice(&std::fs::read(
            std::env::args()
                .nth(1)
                .context("Private fixture configuration required")?,
        )?)?;
        let cleanup_only = std::env::args().nth(2).as_deref() == Some("--cleanup");
        ensure!(
            !cleanup_only
                || (cfg.state.join("intent.json").exists()
                    && cfg.state.join("binding.json").exists()),
            "Cleanup requires original intent and binding; never provision a replacement"
        );
        ensure!(
            matches!(
                reqwest::Url::parse(&cfg.endpoint)?.host_str(),
                Some("localhost" | "127.0.0.1")
            ),
            "Local fixture gateway only"
        );
        ensure!(
            cfg.host.ends_with(".svc.cluster.local:8443"),
            "Controlled local Git fixture only"
        );
        std::fs::create_dir_all(&cfg.state)?;
        let plain = OpenShell::new(
            "openshell".into(),
            "git-c6-local".into(),
            ExecConnectionConfig {
                endpoint: cfg.endpoint.clone(),
                certificates: cfg.certificates.clone(),
                tls_server_name: Some(cfg.tls_server_name.clone()),
                workspace: "default".into(),
            },
            cfg.image.clone(),
        )?;
        let pin_path = cfg.state.join("provider.json");
        let pin = if pin_path.exists() {
            serde_json::from_slice(&std::fs::read(&pin_path)?)?
        } else {
            let pin = plain.pin_credential_provider(&cfg.provider).await?;
            ensure!(
                pin == plain.pin_credential_provider(&cfg.provider).await?,
                "Provider profile snapshot changed"
            );
            save(&pin_path, &pin)?;
            pin
        };
        let p = plain.with_credential_provider(pin)?;
        let record_path = cfg.state.join("intent.json");
        let fresh = !record_path.exists();
        let record: Record = if fresh {
            let r = Record {
                id: openworker_runtime::id("env"),
                request_id: openworker_runtime::id("gitfixture"),
                actor: "local-qualification".into(),
                spec: p.spec(Sharing::Exclusive, Management::Harness),
                phase: Phase::Provisioning,
                binding: None,
                revision: 1,
            };
            save(&record_path, &r)?;
            r
        } else {
            serde_json::from_slice(&std::fs::read(&record_path)?)?
        };
        let binding_path = cfg.state.join("binding.json");
        let b = if binding_path.exists() {
            serde_json::from_slice(&std::fs::read(&binding_path)?)?
        } else {
            let b = if fresh {
                p.create(record.clone()).await?
            } else {
                let raw = record
                    .id
                    .strip_prefix("env_")
                    .context("Invalid fixture environment ID")?;
                p.bind_existing(&record, &format!("owr-{}", &raw[..15]))
                    .await?
            };
            save(&binding_path, &b)?;
            b
        };
        if cleanup_only {
            p.delete(b.clone(), uuid::Uuid::new_v4().to_string())
                .await?;
            ensure!(
                p.confirm_deleted(b).await?,
                "Original fixture deletion not confirmed"
            );
            save(
                &cfg.state.join("cleanup.json"),
                &json!({"original_deleted":true}),
            )?;
            println!("PASS original fixture cleanup");
            return Ok(());
        }
        let result=async {
        let mut c=connection(&p,&b,&cfg.host).await?;
        let run=async {
            let v=shell(&mut c,"case \"$GITHUB_TOKEN\" in openshell:resolve:env:*) printf 'placeholder-only';; *) exit 19;; esac").await?;
            ensure!(v["exit_code"]==0 && v["stdout"]=="placeholder-only","Sandbox Git did not receive a placeholder");
            let clone=format!("git clone -q https://{}/org/project.git checkout && test \"$(cat checkout/proof.txt)\" = 'original source' && test \"$(git -C checkout remote get-url origin)\" = 'https://{}/org/project.git' && ! grep -Eq 'credential|openshell:resolve|fixture-' checkout/.git/config && printf 'clone-ok'",cfg.host,cfg.host);
            let v=shell(&mut c,&clone).await?;
            if v["exit_code"]!=0 {save(&cfg.state.join("clone-failure.json"),&v)?;}
            ensure!(v["exit_code"]==0 && v["stdout"]=="clone-ok","Git HTTPS clone failed; inspect private fixture diagnostic");
            let v=shell(&mut c,"git -C checkout fetch -q origin && printf 'fetch-ok'").await?;
            ensure!(v["exit_code"]==0 && v["stdout"]=="fetch-ok","Git fetch failed");
            let push=format!("printf 'child change\\n' > checkout/proof.txt && git -C checkout add proof.txt && git -C checkout -c user.name=Fixture -c user.email=f@example.invalid commit -qm child && git -C checkout push origin HEAD:refs/heads/agents/probe-{}",&record.id[4..19]);
            let v=shell(&mut c,&push).await?;
            ensure!((v["exit_code"]==0)==cfg.publication,"Publication did not match the explicit provider policy");
            let v=shell(&mut c,&format!("git ls-remote https://{}/org/other.git",cfg.host)).await?;
            ensure!(v["exit_code"].as_i64().is_some_and(|n|n!=0),"Unconfigured repository received Git access");
            if cfg.barrier {
                barrier(&cfg,"expired").await?;
                let v=shell(&mut c,"git -C checkout fetch -q origin").await?;
                ensure!(v["exit_code"].as_i64().is_some_and(|n|n!=0),"Expired credential still worked");
                barrier(&cfg,"refreshed").await?;
                // A new tool process gets the new OpenShell reference; the
                // existing sandbox-local manager must use it for NEW commands.
                let replacement=connection(&p,&b,&cfg.host).await?;
                let old=std::mem::replace(&mut c,replacement);
                let _=old.close().await;
                let v=shell(&mut c,"git -C checkout fetch -q origin && printf 'refreshed-ok'").await?;
                ensure!(v["exit_code"]==0 && v["stdout"]=="refreshed-ok","New provider reference did not reach existing process manager");
                barrier(&cfg,"revoked").await?;
                let v=shell(&mut c,"git -C checkout fetch -q origin").await?;
                ensure!(v["exit_code"].as_i64().is_some_and(|n|n!=0),"Detached provider still permitted Git access");
                ensure!(p.inspect(b.clone()).await.is_err(),"Changed attachment still permitted a new tool connection");
            }
            Ok::<_,anyhow::Error>(())
        }.await;
        let _=c.close().await;
        run
    }.await;
        if result.is_err() && cfg.retain_failed {
            save(
                &cfg.state.join("retained-failure.json"),
                &json!({"retained_original":true,"environment_id":b.environment_id,"sandbox":b.remote_name}),
            )?;
            return result;
        }
        // Always try cleanup of this exact original binding. A profile/provider
        // change is not permission to bind/delete a replacement resource.
        let cleanup = p.delete(b.clone(), uuid::Uuid::new_v4().to_string()).await;
        if cleanup.is_ok() {
            ensure!(
                p.confirm_deleted(b).await?,
                "Fixture sandbox deletion not confirmed"
            )
        }
        result?;
        cleanup?;
        save(
            &cfg.state.join("result.json"),
            &json!({"passed":true,"clone":true,"fetch":true,"publication_allowed":cfg.publication,"unconfigured_repo_denied":true,"placeholder_only":true,"credential_expiry_refresh":cfg.barrier,"credential_detach_revocation":cfg.barrier,"cleanup_confirmed":true}),
        )?;
        println!("PASS controlled OpenShell Git HTTPS, placeholder authentication, explicit publication policy and cleanup");
        Ok(())
    }
}
#[cfg(not(windows))]
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    probe::run().await
}
#[cfg(windows)]
fn main() {
    eprintln!("This local OpenShell qualification runs on Linux or macOS");
}
