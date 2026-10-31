use anyhow::{ensure, Context, Result};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::Path,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio_util::sync::CancellationToken;

#[derive(Default)]
struct Log {
    bytes: Vec<u8>,
    dropped: u64,
}
impl Log {
    #[cfg(target_os = "linux")]
    fn push(&mut self, chunk: &[u8]) {
        self.bytes.extend_from_slice(chunk);
        if self.bytes.len() > 24000 {
            let n = self.bytes.len() - 24000;
            self.bytes.drain(..n);
            self.dropped += n as u64;
        }
    }
}
struct Status {
    state: &'static str,
    exit_code: Option<i32>,
}
struct Process {
    status: Arc<Mutex<Status>>,
    stdout: Arc<Mutex<Log>>,
    stderr: Arc<Mutex<Log>>,
    stop: CancellationToken,
    task: tokio::task::JoinHandle<()>,
}
#[derive(Default)]
pub(super) struct Processes {
    entries: BTreeMap<String, Process>,
}
impl Drop for Processes {
    fn drop(&mut self) {
        for p in self.entries.values() {
            p.stop.cancel();
        }
    }
}
impl Processes {
    #[cfg(target_os = "linux")]
    pub fn active(&self) -> bool {
        self.entries.values().any(|p| !p.task.is_finished())
    }
    pub async fn call(&mut self, root: &Path, name: &str, args: &Value) -> Result<Value> {
        self.call_as(root, name, args, None).await
    }
    pub async fn call_as(
        &mut self,
        root: &Path,
        name: &str,
        args: &Value,
        launch_id: Option<&str>,
    ) -> Result<Value> {
        if matches!(name, "run_shell" | "process_start") {
            let id = self.start(root, args, name == "run_shell", launch_id)?;
            if name == "run_shell" {
                let wait = args["yield_ms"].as_u64().unwrap_or(1000);
                let p = self.entries.get_mut(&id).unwrap();
                let _ = tokio::time::timeout(Duration::from_millis(wait), &mut p.task).await;
            }
            return self.snapshot(&id, true);
        }
        let id = args["process_id"].as_str().context("process_id required")?;
        ensure!(
            self.entries.contains_key(id),
            "Unknown process: handles belong to this runner/session and do not survive restart"
        );
        if name == "process_stop" {
            let p = self.entries.get_mut(id).unwrap();
            p.stop.cancel();
            if !p.task.is_finished() {
                let _ = tokio::time::timeout(Duration::from_secs(3), &mut p.task).await;
            }
        }
        self.snapshot(id, name == "process_logs")
    }
    fn snapshot(&self, id: &str, logs: bool) -> Result<Value> {
        let p = self.entries.get(id).context("Unknown process")?;
        let status = p.status.lock().unwrap();
        let mut v = json!({"process_id":id,"status":status.state,"exit_code":status.exit_code});
        if logs {
            for (key, log) in [("stdout", &p.stdout), ("stderr", &p.stderr)] {
                let log = log.lock().unwrap();
                // JSON escaping can multiply byte size; keep the wire response bounded.
                let text = String::from_utf8_lossy(&log.bytes);
                let mut tail: String = text.chars().rev().take(4000).collect();
                tail = tail.chars().rev().collect();
                v[key] = json!(tail);
                v[format!("{key}_truncated")] = json!(log.dropped > 0 || tail.len() < text.len());
            }
        }
        Ok(v)
    }
    pub async fn shutdown(&mut self) {
        for p in self.entries.values() {
            p.stop.cancel();
        }
        for p in self.entries.values_mut() {
            if !p.task.is_finished() {
                let _ = tokio::time::timeout(Duration::from_secs(3), &mut p.task).await;
            }
        }
    }
    #[cfg(not(target_os = "linux"))]
    fn start(&mut self, _: &Path, _: &Value, _: bool, _: Option<&str>) -> Result<String> {
        anyhow::bail!("Managed coding processes currently require Linux; native macOS/Windows lifecycle support is not enabled")
    }
    #[cfg(target_os = "linux")]
    fn start(
        &mut self,
        root: &Path,
        args: &Value,
        foreground: bool,
        launch_id: Option<&str>,
    ) -> Result<String> {
        use std::process::Stdio;
        use tokio::io::AsyncReadExt;
        let command = crate::runner::bounded_text(args, "command", 16000)?;
        ensure!(!command.trim().is_empty(), "Nonempty command required");
        ensure!(
            self.entries
                .values()
                .filter(|p| !p.task.is_finished())
                .count()
                < 8,
            "Session process capacity reached (8)"
        );
        if self.entries.len() >= 64 {
            if let Some(id) = self
                .entries
                .iter()
                .find(|(_, p)| p.task.is_finished())
                .map(|(id, _)| id.clone())
            {
                self.entries.remove(&id);
            }
        }
        let timeout =
            args["timeout_seconds"]
                .as_u64()
                .unwrap_or(if foreground { 60 } else { 3600 });
        ensure!(
            (1..=86400).contains(&timeout),
            "timeout_seconds must be 1..86400"
        );
        let id = launch_id
            .map(str::to_owned)
            .unwrap_or_else(|| crate::id("process"));
        ensure!(
            !self.entries.contains_key(&id),
            "Launch identity already used; inspect its status, do not replay"
        );
        let mut cmd = tokio::process::Command::new("/bin/sh");
        cmd.args(["-c", command])
            .current_dir(root)
            .env_clear()
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .env("HOME", "/tmp")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .process_group(0);
        // Only sandbox proxy/CA plumbing; never inherit model keys from the host.
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
            "NODE_EXTRA_CA_CERTS",
            "NODE_USE_ENV_PROXY",
            "GIT_SSL_CAINFO",
        ] {
            if let Some(value) = std::env::var_os(key) {
                cmd.env(key, value);
            }
        }
        let mut child = cmd.spawn()?;
        let group = Group(child.id().context("Missing child PID")?);
        let stdout = Arc::new(Mutex::new(Log::default()));
        let stderr = Arc::new(Mutex::new(Log::default()));
        async fn drain<R: tokio::io::AsyncRead + Unpin>(mut stream: R, log: Arc<Mutex<Log>>) {
            let mut chunk = [0; 4096];
            loop {
                match stream.read(&mut chunk).await {
                    Ok(0) | Err(_) => break,
                    Ok(n) => log.lock().unwrap().push(&chunk[..n]),
                }
            }
        }
        let out = tokio::spawn(drain(child.stdout.take().unwrap(), stdout.clone()));
        let err = tokio::spawn(drain(child.stderr.take().unwrap(), stderr.clone()));
        let stop = CancellationToken::new();
        let cancel = stop.clone();
        let status = Arc::new(Mutex::new(Status {
            state: "running",
            exit_code: None,
        }));
        let state = status.clone();
        let task = tokio::spawn(async move {
            let reason = tokio::select! { biased;
                _ = cancel.cancelled() => "stopped",
                _ = tokio::time::sleep(Duration::from_secs(timeout)) => "timed_out",
                result = exited_without_reaping(group.0) => match result {
                    Ok(()) => "exited",
                    Err(_) => "failed",
                },
            };
            // The unreaped child reserves the process-group ID during cleanup.
            // Reaping first could allow PID reuse before signalling the group.
            drop(group);
            let exit = child.wait().await.ok().and_then(|s| s.code());
            let mut out = out;
            let mut err = err;
            if tokio::time::timeout(Duration::from_secs(1), &mut out)
                .await
                .is_err()
            {
                out.abort();
            }
            if tokio::time::timeout(Duration::from_secs(1), &mut err)
                .await
                .is_err()
            {
                err.abort();
            }
            *state.lock().unwrap() = Status {
                state: reason,
                exit_code: exit,
            };
        });
        self.entries.insert(
            id.clone(),
            Process {
                status,
                stdout,
                stderr,
                stop,
                task,
            },
        );
        Ok(id)
    }
}
#[cfg(target_os = "linux")]
async fn exited_without_reaping(pid: u32) -> Result<()> {
    loop {
        let mut info = std::mem::MaybeUninit::<libc::siginfo_t>::zeroed();
        let result = unsafe {
            libc::waitid(
                libc::P_PID,
                pid,
                info.as_mut_ptr(),
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        };
        if result < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        if unsafe { info.assume_init().si_pid() } != 0 {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}
#[cfg(target_os = "linux")]
struct Group(u32);
#[cfg(target_os = "linux")]
impl Drop for Group {
    fn drop(&mut self) {
        unsafe {
            libc::kill(-(self.0 as i32), libc::SIGKILL);
        }
    }
}

#[cfg(all(test, target_os = "linux"))]
mod resilience_tests {
    use super::*;
    async fn wait(p: &Processes, id: &str, expected: &str) {
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                if p.snapshot(id, false).unwrap()["status"] == expected {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap();
    }
    #[tokio::test]
    async fn resilience_stopping_one_workspace_does_not_stop_the_other() {
        let a = tempfile::tempdir().unwrap();
        let b = tempfile::tempdir().unwrap();
        let mut one = Processes::default();
        let mut two = Processes::default();
        let args = json!({"command":"echo owned; sleep 30", "timeout_seconds":30});
        let x = one.call(a.path(), "process_start", &args).await.unwrap();
        let y = two.call(b.path(), "process_start", &args).await.unwrap();
        let xi = x["process_id"].as_str().unwrap();
        let yi = y["process_id"].as_str().unwrap();
        assert!(two
            .call(b.path(), "process_stop", &json!({"process_id":xi}))
            .await
            .is_err());
        one.shutdown().await;
        assert_eq!(one.snapshot(xi, false).unwrap()["status"], "stopped");
        assert_eq!(two.snapshot(yi, false).unwrap()["status"], "running");
        two.shutdown().await;
    }
    #[tokio::test]
    async fn resilience_deadline_kills_descendants_without_a_status_request() {
        let r = tempfile::tempdir().unwrap();
        let mut p = Processes::default();
        // The descendant tries to write after the parent timeout. Polling a
        // result is not what enforces the deadline.
        let x=p.call(r.path(),"process_start",&json!({"command":"(sleep 2; echo orphan > orphan.txt) & wait","timeout_seconds":1})).await.unwrap();
        tokio::time::sleep(Duration::from_millis(2400)).await;
        assert!(!r.path().join("orphan.txt").exists());
        wait(&p, x["process_id"].as_str().unwrap(), "timed_out").await;
        p.shutdown().await;
    }
    #[tokio::test]
    async fn resilience_shutdown_stops_group_and_retains_bounded_logs() {
        let r = tempfile::tempdir().unwrap();
        let mut p = Processes::default();
        let x=p.call(r.path(),"process_start",&json!({"command":"printf ready; (sleep 2; echo orphan > orphan.txt) & wait","timeout_seconds":30})).await.unwrap();
        let id = x["process_id"].as_str().unwrap();
        tokio::time::timeout(Duration::from_secs(3), async {
            while !p.snapshot(id, true).unwrap()["stdout"]
                .as_str()
                .unwrap()
                .contains("ready")
            {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap();
        p.shutdown().await;
        assert_eq!(p.snapshot(id, true).unwrap()["status"], "stopped");
        assert_eq!(p.snapshot(id, true).unwrap()["stdout"], "ready");
        tokio::time::sleep(Duration::from_millis(2200)).await;
        assert!(!r.path().join("orphan.txt").exists());
    }
}
