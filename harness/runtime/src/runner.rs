//! Unprivileged native MCP example/tool process. No model keys or policy decisions.
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::path::{Component, Path, PathBuf};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};

pub fn schemas() -> Vec<Value> {
    vec![
        json!({"name":"read_file","description":"Read a UTF-8 file in the granted workspace","inputSchema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"],"additionalProperties":false}}),
        json!({"name":"write_file","description":"Write a UTF-8 file in the granted workspace","inputSchema":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"],"additionalProperties":false}}),
        json!({"name":"run_shell","description":"Run a shell command in the working directory. Isolation depends on the host-selected execution backend; a working directory alone does not constrain file or network access.","inputSchema":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"],"additionalProperties":false}}),
        json!({"name":"example_echo","description":"Example ExampleApp-provided tool: returns the supplied text with partner attribution","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false}}),
    ]
}

pub fn bounded_text<'a>(v: &'a Value, key: &str, max: usize) -> Result<&'a str> {
    let s = v[key]
        .as_str()
        .with_context(|| format!("{key} must be a string"))?;
    if s.len() > max {
        bail!("{key} exceeds limit");
    }
    Ok(s)
}
pub fn relative_path(relative: &str) -> Result<&Path> {
    let p = Path::new(relative);
    crate::platform::validate_path(p)?;
    if relative.is_empty()
        || relative.len() > 512
        || !p.components().all(|c| matches!(c, Component::Normal(_)))
    {
        bail!("Relative workspace file required");
    }
    Ok(p)
}
pub fn path(root: &Path, relative: &str) -> Result<PathBuf> {
    let p = relative_path(relative)?;
    let mut target = root.to_owned();
    for part in p.components() {
        target.push(part);
        if target
            .symlink_metadata()
            .is_ok_and(|m| crate::platform::linked(&m))
        {
            bail!("Symlinks are refused");
        }
    }
    Ok(target)
}

pub async fn call(root: &Path, name: &str, args: &Value) -> Result<Value> {
    if !args.is_object() {
        bail!("Tool arguments must be an object");
    }
    match name {
        "example_echo" => {
            Ok(json!({"partner":"ExampleApp","text":bounded_text(args,"text",16000)?}))
        }
        "read_file" => {
            let p = path(root, bounded_text(args, "path", 512)?)?;
            if p.metadata()?.len() > 128000 {
                bail!("File exceeds 128 KB");
            }
            Ok(
                json!({"path":args["path"],"text":String::from_utf8(crate::bundle::read_limited(&p,128000)?)?}),
            )
        }
        "write_file" => {
            let p = path(root, bounded_text(args, "path", 512)?)?;
            let text = bounded_text(args, "content", 128000)?;
            if let Some(parent) = p.parent() {
                std::fs::create_dir_all(parent)?;
            }
            // O_NOFOLLOW closes the final-component symlink race. Parent races are
            // additionally bounded by the OpenShell filesystem policy, not by this helper.
            let mut options = std::fs::OpenOptions::new();
            options.write(true).create(true).truncate(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.custom_flags(libc::O_NOFOLLOW).mode(0o600);
            }
            use std::io::Write;
            #[cfg(windows)]
            {
                use std::os::windows::fs::OpenOptionsExt;
                options.custom_flags(
                    windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT,
                );
            }
            let mut file = options.open(p)?;
            if crate::platform::linked(&file.metadata()?) {
                bail!("Reparse point refused");
            }
            file.write_all(text.as_bytes())?;
            Ok(json!({"path":args["path"],"bytes":text.len(),"written":true}))
        }
        #[cfg(windows)]
        "run_shell" => {
            crate::platform::windows::shell(root, bounded_text(args, "command", 16000)?).await
        }
        #[cfg(not(windows))]
        "run_shell" => {
            let command = bounded_text(args, "command", 16000)?;
            let mut cmd = tokio::process::Command::new("/bin/sh");
            cmd.args(["-c", command])
                .current_dir(root)
                .env_clear()
                .env("PATH", "/usr/local/bin:/usr/bin:/bin")
                .env("HOME", "/tmp")
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .kill_on_drop(true);
            // OpenShell supplies its policy-enforcing proxy/CA environment to
            // sandbox processes. Preserve only that plumbing for child CLIs.
            // Direct supervisors clear these variables before launching this MCP;
            // never forward the controller environment or any provider keys.
            for key in [
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "REQUESTS_CA_BUNDLE",
                "CURL_CA_BUNDLE",
                "grpc_proxy",
                "NODE_USE_ENV_PROXY",
                "NODE_EXTRA_CA_CERTS",
                "DENO_CERT",
                "GIT_SSL_CAINFO",
            ] {
                if let Some(value) = std::env::var_os(key) {
                    cmd.env(key, value);
                }
            }
            #[cfg(unix)]
            {
                cmd.process_group(0);
            }
            let mut child = cmd.spawn()?;
            let pid = child.id();
            let out = child.stdout.take().unwrap();
            let err = child.stderr.take().unwrap();
            async fn read<R: tokio::io::AsyncRead + Unpin>(r: R) -> Result<Vec<u8>> {
                let mut bytes = vec![];
                r.take(64001).read_to_end(&mut bytes).await?;
                if bytes.len() > 64000 {
                    bail!("Tool output exceeds 64 KB");
                }
                Ok(bytes)
            }
            let result=tokio::time::timeout(std::time::Duration::from_secs(60),async { let (out,err,status)=tokio::try_join!(read(out),read(err),async {Ok::<_,anyhow::Error>(child.wait().await?)})?;
                Ok::<_,anyhow::Error>(json!({"stdout":String::from_utf8_lossy(&out),"stderr":String::from_utf8_lossy(&err),"exit_code":status.code()})) }).await;
            // Reap descendants, including background children, when the command ends.
            #[cfg(unix)]
            if let Some(pid) = pid {
                unsafe {
                    libc::kill(-(pid as i32), libc::SIGKILL);
                }
            }
            result.context("Sandbox command timed out after 60 seconds")?
        }
        _ => bail!("Unknown MCP tool"),
    }
}

pub async fn serve(root: &Path) -> Result<()> {
    serve_labeled(root, "example").await
}

pub async fn serve_labeled(root: &Path, label: &str) -> Result<()> {
    if !crate::bundle::safe_name(label) {
        bail!("Invalid example server label");
    }
    let root = root.canonicalize()?;
    let mut input = BufReader::new(tokio::io::stdin());
    let mut output = tokio::io::stdout();
    let mut initialized = false;
    loop {
        let mut frame = vec![];
        let n = (&mut input)
            .take((crate::MAX_FRAME + 1) as u64)
            .read_until(b'\n', &mut frame)
            .await?;
        if n == 0 {
            break;
        }
        if n > crate::MAX_FRAME {
            bail!("MCP frame too large");
        }
        let v: Value = serde_json::from_slice(&frame)?;
        if v["method"] == "notifications/initialized" {
            continue;
        }
        if v.get("id").is_none() {
            continue;
        }
        let result = match v["method"].as_str().unwrap_or("") {
            "initialize" => {
                initialized = true;
                Ok(
                    json!({"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"example-native-example","version":"0.1.0"}}),
                )
            }
            "tools/list" if initialized => Ok(json!({"tools":schemas()})),
            "tools/call" if initialized => match call(
                &root,
                v["params"]["name"].as_str().unwrap_or(""),
                &v["params"]["arguments"],
            )
            .await
            {
                Ok(mut value) => {
                    if v["params"]["name"] == "example_echo" && label != "example" {
                        value["server_label"] = json!(label);
                    }
                    Ok(
                        json!({"content":[{"type":"text","text":value.to_string()}],"isError":false}),
                    )
                }
                Err(e) => {
                    Ok(json!({"content":[{"type":"text","text":e.to_string()}],"isError":true}))
                }
            },
            _ => Err(anyhow::anyhow!(
                "Initialize first or unsupported MCP method"
            )),
        };
        let response = match result {
            Ok(r) => json!({"jsonrpc":"2.0","id":v["id"],"result":r}),
            Err(e) => {
                json!({"jsonrpc":"2.0","id":v["id"],"error":{"code":-32601,"message":e.to_string()}})
            }
        };
        output.write_all(format!("{response}\n").as_bytes()).await?;
        output.flush().await?;
    }
    Ok(())
}
