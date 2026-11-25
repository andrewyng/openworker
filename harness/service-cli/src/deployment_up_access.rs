//! Private management routing. Only the original cluster's TLS endpoint is
//! forwarded; this proxy is not a general-purpose internet or credential proxy.
use super::*;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

pub(in super::super) struct Bridge {
    child: tokio::process::Child,
    proxy: tokio::task::JoinHandle<()>,
}
impl Drop for Bridge {
    fn drop(&mut self) {
        self.proxy.abort();
        let _ = self.child.start_kill();
    }
}
fn endpoint(kube: &Value) -> Result<String> {
    let clusters = kube["clusters"]
        .as_array()
        .filter(|a| a.len() == 1)
        .context("Expected one original cluster")?;
    let c = &clusters[0]["cluster"];
    ensure!(
        c["insecure-skip-tls-verify"] != true
            && c["certificate-authority-data"]
                .as_str()
                .is_some_and(|s| !s.is_empty()),
        "Original cluster CA required"
    );
    let u = reqwest::Url::parse(c["server"].as_str().context("Missing cluster endpoint")?)?;
    ensure!(
        u.scheme() == "https"
            && u.port_or_known_default() == Some(443)
            && u.username().is_empty()
            && u.password().is_none()
            && u.path() == "/"
            && u.query().is_none(),
        "Invalid private management endpoint"
    );
    Ok(u.host_str().context("Endpoint host missing")?.into())
}
fn validate_connect(line: &str, host: &str) -> Result<()> {
    let target = line.strip_prefix("CONNECT ").and_then(|rest| {
        rest.strip_suffix(" HTTP/1.1")
            .or_else(|| rest.strip_suffix(" HTTP/1.0"))
    });
    // DNS names are case-insensitive. URL parsing normalizes the reviewed host,
    // while kubectl preserves the uppercase label returned by some EKS endpoints.
    // Keep the method, port and exact destination restriction unchanged.
    ensure!(
        target.is_some_and(|target| target.eq_ignore_ascii_case(&format!("{host}:443"))),
        "Proxy destination is outside the reviewed cluster"
    );
    Ok(())
}
async fn tunnel(mut incoming: tokio::net::TcpStream, host: &str, port: u16) -> Result<()> {
    let mut header = Vec::new();
    loop {
        let mut b = [0];
        incoming.read_exact(&mut b).await?;
        header.push(b[0]);
        ensure!(header.len() <= 8192, "Proxy header too large");
        if header.ends_with(b"\r\n\r\n") {
            break;
        }
    }
    let line = std::str::from_utf8(&header)?
        .split("\r\n")
        .next()
        .unwrap_or("");
    validate_connect(line, host)?;
    let mut upstream =
        tokio::net::TcpStream::connect((std::net::Ipv4Addr::LOCALHOST, port)).await?;
    incoming
        .write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        .await?;
    tokio::io::copy_bidirectional(&mut incoming, &mut upstream).await?;
    Ok(())
}
impl Native<'_> {
    async fn probe(&self, kubeconfig: &Path) -> Result<Value> {
        let raw = self
            .tool(
                "kubectl",
                vec![
                    "--kubeconfig".into(),
                    arg(kubeconfig)?,
                    "--request-timeout=10s".into(),
                    "get".into(),
                    "namespace".into(),
                    "kube-system".into(),
                    "-o".into(),
                    "json".into(),
                ],
                None,
            )
            .await?;
        let v: Value = serde_json::from_slice(&raw)?;
        ensure!(
            v["metadata"]["name"] == "kube-system" && v["metadata"]["uid"].is_string(),
            "Cluster identity probe failed"
        );
        Ok(v)
    }
    async fn session_json(&self, args: Vec<String>) -> Result<Value> {
        let mut a = args;
        a.extend([
            "--profile".into(),
            self.aws_profile()?,
            "--region".into(),
            self.review.config.cloud.region().into(),
            "--output".into(),
            "json".into(),
            "--no-cli-pager".into(),
        ]);
        serde_json::from_slice(&self.tool("aws", a, None).await?).context("Invalid SSM observation")
    }
    async fn retire_session(&self, path: &Path) -> Result<()> {
        if !path.exists() {
            return Ok(());
        }
        let mut r = self.document(path)?;
        ensure!(
            r["deploymentId"] == self.review.deployment_id,
            "SSM receipt identity mismatch"
        );
        if r["phase"] == "terminated" {
            return Ok(());
        }
        let mut found = vec![];
        for state in ["Active", "History"] {
            let v = self
                .session_json(strings(&["ssm", "describe-sessions", "--state", state]))
                .await?;
            for s in v["Sessions"]
                .as_array()
                .context("SSM session inventory missing")?
            {
                if s["Reason"] == r["reason"] && s["Target"] == r["target"] {
                    found.push(s.clone());
                }
            }
        }
        ensure!(found.len()==1,"Original SSM session is not uniquely observed. Retain receipt; do not start another session.");
        let s = &found[0];
        let id = text(s, "SessionId")?;
        ensure!(
            r.get("sessionId").is_none_or(|v| v == &id),
            "SSM session changed"
        );
        r["sessionId"] = json!(id);
        write_private(path, &r)?;
        if !["Terminated", "Failed"].contains(&s["Status"].as_str().unwrap_or("")) {
            self.session_json(strings(&["ssm", "terminate-session", "--session-id", &id]))
                .await?;
        }
        r["phase"] = json!("terminated");
        write_private(path, &r)?;
        Ok(())
    }
    pub(in super::super) async fn close_access(&self) -> Result<()> {
        self.bridge.borrow_mut().take();
        if self.provider() == "aws" {
            self.retire_session(&self.root.join("ssm-session.json"))
                .await?;
        }
        Ok(())
    }
    pub(super) async fn connect_private(&self, kubeconfig: &Path) -> Result<()> {
        let mut kube = self.document(kubeconfig)?;
        let host = endpoint(&kube)?;
        if self.bridge.borrow().is_some()
            || kube["clusters"][0]["cluster"]["proxy-url"].is_null()
                && self.probe(kubeconfig).await.is_ok()
        {
            let value = self.probe(kubeconfig).await?;
            return self.access_receipt(kubeconfig, &value);
        }
        let (plan, _) = self.plan()?;
        let has_bridge = if self.provider() == "aws" {
            plan.infrastructure
                .as_ref()
                .unwrap()
                .operator_access
                .is_some()
        } else {
            plan.azure_infrastructure
                .as_ref()
                .unwrap()
                .management_access
                .is_some()
        };
        ensure!(has_bridge,"Private management API unreachable. Connect the approved private network, or configure the operator bridge before creating a new deployment. Original resources retained.");
        let config_path = self.root.join("private-route.json");
        let route = if config_path.exists() {
            self.document(&config_path)?
        } else {
            let port = std::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))?
                .local_addr()?
                .port();
            let v = json!({"deploymentId":self.review.deployment_id,"host":host,"proxyPort":port});
            write_private(&config_path, &v)?;
            v
        };
        ensure!(
            route["host"] == host && route["deploymentId"] == self.review.deployment_id,
            "Private route target changed"
        );
        let proxy_port = u16::try_from(route["proxyPort"].as_u64().context("Proxy port missing")?)?;
        let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, proxy_port))
            .await
            .context(
                "Original loopback proxy port is occupied; do not alter the pinned kubeconfig",
            )?;
        let raw_port = std::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))?
            .local_addr()?
            .port();
        let (program, args) = if self.provider() == "azure" {
            let m = self.azure_material().await?;
            let ip = text(&m, "managementIp")?;
            ip.parse::<std::net::Ipv4Addr>()
                .context("Invalid owned management IP")?;
            let mut a = vec![
                "-N".into(),
                "-T".into(),
                "-o".into(),
                "BatchMode=yes".into(),
                "-o".into(),
                "ExitOnForwardFailure=yes".into(),
                "-o".into(),
                "ForwardAgent=no".into(),
                "-o".into(),
                "StrictHostKeyChecking=accept-new".into(),
                "-o".into(),
                format!(
                    "UserKnownHostsFile={}",
                    self.root.join("ssh-known-hosts").display()
                ),
                "-L".into(),
                format!("127.0.0.1:{raw_port}:{host}:443"),
            ];
            if let Some(key) = self
                .review
                .config
                .advanced
                .azure
                .as_ref()
                .and_then(|a| a.ssh_private_key_file.as_ref())
            {
                a.extend(["-i".into(), arg(key)?]);
            }
            a.push(format!("owsadmin@{ip}"));
            ("ssh", a)
        } else {
            let path = self.root.join("ssm-session.json");
            self.retire_session(&path).await?;
            if path.exists() {
                fs::rename(
                    &path,
                    self.root.join(format!(
                        "ssm-session-{}.json",
                        uuid::Uuid::new_v4().simple()
                    )),
                )?;
            }
            let status = self.cloud_status().await?;
            let target = text(
                &status["observed"]["infra"]["outputs"],
                "OperatorInstanceId",
            )?;
            ensure!(
                target.starts_with("i-") && target[2..].bytes().all(|b| b.is_ascii_hexdigit()),
                "Invalid original operator instance"
            );
            let reason = format!("ows-{}", uuid::Uuid::new_v4().simple());
            write_private(
                &path,
                &json!({"deploymentId":self.review.deployment_id,"target":target,"reason":reason,"phase":"intent"}),
            )?;
            (
                "aws",
                vec![
                    "ssm".into(),
                    "start-session".into(),
                    "--target".into(),
                    target,
                    "--document-name".into(),
                    "AWS-StartPortForwardingSessionToRemoteHost".into(),
                    "--reason".into(),
                    reason,
                    "--parameters".into(),
                    serde_json::to_string(
                        &json!({"host":[host],"portNumber":["443"],"localPortNumber":[raw_port.to_string()]}),
                    )?,
                    "--profile".into(),
                    self.aws_profile()?,
                    "--region".into(),
                    self.review.config.cloud.region().into(),
                ],
            )
        };
        let mut command = tokio::process::Command::new(program);
        command
            .args(args)
            .envs(self.environment()?)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true);
        let mut child = command.spawn().context("Start private management bridge")?;
        let mut ready = false;
        for _ in 0..30 {
            ensure!(child.try_wait()?.is_none(),"Private bridge exited; check CLI login, bridge permissions and SSH key/plugin prerequisites");
            if tokio::net::TcpStream::connect((std::net::Ipv4Addr::LOCALHOST, raw_port))
                .await
                .is_ok()
            {
                ready = true;
                break;
            }
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        }
        ensure!(ready, "Private management bridge did not become ready");
        let proxy = tokio::spawn(async move {
            while let Ok((socket, _)) = listener.accept().await {
                let host = host.clone();
                tokio::spawn(async move {
                    let _ = tunnel(socket, &host, raw_port).await;
                });
            }
        });
        *self.bridge.borrow_mut() = Some(Bridge { child, proxy });
        let proxy_url = format!("http://127.0.0.1:{proxy_port}");
        let current = &kube["clusters"][0]["cluster"]["proxy-url"];
        ensure!(
            current.is_null() || current == &proxy_url,
            "Unexpected kubeconfig proxy"
        );
        if current.is_null() {
            kube["clusters"][0]["cluster"]["proxy-url"] = json!(proxy_url);
            write_private(kubeconfig, &kube)?;
        }
        let value = self.probe(kubeconfig).await?;
        self.access_receipt(kubeconfig, &value)
    }
    fn access_receipt(&self, kubeconfig: &Path, value: &Value) -> Result<()> {
        let path = self.root.join("private-access.json");
        if path.exists() {
            ensure!(
                self.document(&path)?["clusterUid"] == value["metadata"]["uid"],
                "Original cluster was replaced"
            );
        }
        write_private(
            &path,
            &json!({"kubeconfig":kubeconfig,"clusterUid":value["metadata"]["uid"],"tlsVerified":true}),
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn connect_accepts_dns_case_variation_but_no_other_destination_changes() {
        let host = "3e2d511d.gr7.us-west-2.eks.amazonaws.com";
        for protocol in ["HTTP/1.0", "HTTP/1.1"] {
            assert!(validate_connect(&format!("CONNECT {host}:443 {protocol}"), host).is_ok());
            assert!(validate_connect(
                &format!("CONNECT {}:443 {protocol}", host.to_ascii_uppercase()),
                host
            )
            .is_ok());
        }
        for target in [
            format!("{host}:8443"),
            format!("{host}.evil:443"),
            format!("user@{host}:443"),
            format!("{host}:443/path"),
            format!("{host}:443?query"),
            format!("https://{host}:443"),
            "127.0.0.1:443".into(),
        ] {
            assert!(validate_connect(&format!("CONNECT {target} HTTP/1.1"), host).is_err());
        }
        assert!(validate_connect(&format!("connect {host}:443 HTTP/1.1"), host).is_err());
        assert!(validate_connect(&format!("CONNECT {host}:443 HTTP/2.0"), host).is_err());
    }
    #[test]
    fn private_endpoint_requires_original_ca_and_https() {
        let mut v = json!({"clusters":[{"cluster":{"server":"https://original.internal","certificate-authority-data":"CA"}}]});
        assert_eq!(endpoint(&v).unwrap(), "original.internal");
        v["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = json!(true);
        assert!(endpoint(&v).is_err());
        v["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = json!(false);
        v["clusters"][0]["cluster"]["server"] = json!("https://evil@original.internal");
        assert!(endpoint(&v).is_err());
    }
}
