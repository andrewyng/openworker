//! Bounded trusted Git subprocesses. Never forward raw stderr or credentials.
use anyhow::{ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use std::{path::Path, process::Stdio, time::Duration};
use tokio::{
    io::AsyncReadExt,
    process::{Child, Command},
};
use tokio_util::sync::CancellationToken;
use zeroize::Zeroizing;
#[derive(Debug)]
pub(super) struct CommandFailure {
    pub code: Option<i32>,
}
impl std::fmt::Display for CommandFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(
            "Repository command failed; inspect original operation before choosing a new action",
        )
    }
}
impl std::error::Error for CommandFailure {}
struct Group(Child);
impl Drop for Group {
    fn drop(&mut self) {
        #[cfg(unix)]
        if let Some(pid) = self.0.id().filter(|p| *p > 0 && *p <= i32::MAX as u32) {
            unsafe {
                libc::kill(-(pid as i32), libc::SIGKILL);
            }
        }
    }
}
async fn bounded(reader: impl tokio::io::AsyncRead + Unpin) -> Result<Vec<u8>> {
    let mut data = vec![];
    reader.take(1_048_577).read_to_end(&mut data).await?;
    ensure!(data.len() <= 1_048_576, "Trusted Git output limit");
    Ok(data)
}
fn disk(root: &Path) -> Result<()> {
    let mut stack = vec![root.to_path_buf()];
    let mut bytes = 0u64;
    let mut count = 0usize;
    while let Some(path) = stack.pop() {
        let metadata = std::fs::symlink_metadata(&path)?;
        ensure!(
            !metadata.file_type().is_symlink(),
            "Broker state symlink rejected"
        );
        count += 1;
        ensure!(count <= 20000, "Broker state entry limit");
        if metadata.is_dir() {
            for entry in std::fs::read_dir(path)? {
                stack.push(entry?.path());
            }
        } else {
            ensure!(metadata.is_file(), "Unexpected broker state object");
            bytes = bytes
                .checked_add(metadata.len())
                .context("Broker state size overflow")?;
            ensure!(bytes <= 256 * 1024 * 1024, "Broker state byte limit");
        }
    }
    Ok(())
}
#[allow(clippy::too_many_arguments)] // Explicit network authentication target and TLS trust.
pub(super) async fn run(
    root: &Path,
    args: &[String],
    credential: Option<Zeroizing<String>>,
    origin: &str,
    ca: Option<&Path>,
    stop: &CancellationToken,
    timeout: Duration,
) -> Result<String> {
    ensure!(!stop.is_cancelled(), "Repository operation cancelled");
    let executable = ["/usr/bin/git", "/usr/local/bin/git"]
        .into_iter()
        .map(std::path::PathBuf::from)
        .find(|p| p.is_file())
        .context("Install trusted Git outside agent workspaces")?;
    let mut cmd = Command::new(executable);
    cmd.current_dir(root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_NO_REPLACE_OBJECTS", "1")
        .env("GIT_NO_LAZY_FETCH", "1");
    cmd.args([
        "--no-pager",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
        "-c",
        "credential.interactive=false",
        "-c",
        "http.followRedirects=false",
        "-c",
        "http.sslVerify=true",
        "-c",
        "http.lowSpeedLimit=1",
        "-c",
        "http.lowSpeedTime=20",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        "-c",
        "protocol.file.allow=always",
        "-c",
        "submodule.recurse=false",
        "-c",
        "fetch.fsckObjects=true",
        "-c",
        "transfer.fsckObjects=true",
    ]);
    if let Some(key) = credential.as_ref() {
        let input = Zeroizing::new(format!("x-access-token:{}", key.as_str()));
        let basic = Zeroizing::new(STANDARD.encode(input.as_bytes()));
        let header = Zeroizing::new(format!("Authorization: Basic {}", basic.as_str()));
        cmd.env("GIT_CONFIG_COUNT", "1")
            .env("GIT_CONFIG_KEY_0", format!("http.{origin}.extraHeader"))
            .env("GIT_CONFIG_VALUE_0", header.as_str());
    }
    if let Some(ca) = ca {
        cmd.env("GIT_SSL_CAINFO", ca);
    }
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    #[cfg(unix)]
    cmd.process_group(0);
    let mut child = Group(
        cmd.spawn()
            .map_err(|_| anyhow::anyhow!("Cannot launch trusted Git"))?,
    );
    drop(cmd);
    drop(credential);
    let stdout = child.0.stdout.take().context("Missing Git output")?;
    let stderr = child.0.stderr.take().context("Missing Git diagnostics")?;
    let completion = async {
        let (out, _diagnostics, status) =
            tokio::try_join!(bounded(stdout), bounded(stderr), async {
                Ok::<_, anyhow::Error>(child.0.wait().await?)
            })?;
        if !status.success() {
            return Err(CommandFailure {
                code: status.code(),
            }
            .into());
        }
        String::from_utf8(out).context("Invalid Git response")
    };
    let watchdog = async {
        loop {
            disk(root)?;
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    };
    tokio::select! {biased;_ = stop.cancelled()=>anyhow::bail!("Repository operation cancelled"),_ = tokio::time::sleep(timeout)=>anyhow::bail!("Repository operation timed out; outcome may be unknown"),r=watchdog=>r,r=completion=>r}
}
