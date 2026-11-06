//! Unsandboxed Linux MCP execution with a separate, narrowly owned subreaper.
//! Lifecycle guarantees are not a security boundary against hostile host code.
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::Path, time::Duration};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Identity {
    pub pid: u32,
    pub boot_id: String,
    pub start_ticks: u64,
    pub token: String,
}

#[cfg(target_os = "macos")]
#[path = "direct_macos.rs"]
mod macos;
#[cfg(windows)]
#[path = "direct_windows.rs"]
mod windows;

#[cfg(target_os = "linux")]
mod linux {
    use super::*;
    use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
    pub fn pidfd(pid: u32) -> Result<OwnedFd> {
        if pid == 0 || pid > i32::MAX as u32 {
            bail!("Invalid process identity");
        }
        let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid as libc::pid_t, 0u32) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(unsafe { OwnedFd::from_raw_fd(fd as i32) })
    }
    pub fn identity(pid: u32, token: &str) -> Result<Identity> {
        let stat = std::fs::read_to_string(format!("/proc/{pid}/stat"))?;
        let fields = stat.rsplit_once(')').context("Invalid process stat")?.1;
        let start_ticks = fields
            .split_whitespace()
            .nth(19)
            .context("Missing process start time")?
            .parse()?;
        Ok(Identity {
            pid,
            boot_id: std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?
                .trim()
                .into(),
            start_ticks,
            token: token.into(),
        })
    }
    fn exited(fd: &OwnedFd) -> Result<bool> {
        let mut p = libc::pollfd {
            fd: fd.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        loop {
            if unsafe { libc::poll(&mut p, 1, 0) } >= 0 {
                break;
            }
            let error = std::io::Error::last_os_error();
            if error.kind() != std::io::ErrorKind::Interrupted {
                return Err(error.into());
            }
        }
        Ok(p.revents & (libc::POLLIN | libc::POLLHUP) != 0)
    }
    // Linux clears an exiting process's cmdline. It may exit after the earlier
    // liveness/identity reads, so recheck the SAME pidfd before calling this an
    // ownership failure. A live mismatch must still never receive a signal.
    async fn token_owner(
        cmdline: &[u8],
        token: &str,
        mut exited: impl FnMut() -> Result<bool>,
    ) -> Result<bool> {
        if cmdline.split(|b| *b == 0).any(|s| s == token.as_bytes()) {
            return Ok(true);
        }
        if exited()? {
            return Ok(false);
        }
        if cmdline.is_empty() {
            // The group leader can have released its mm/cmdline while other
            // threads are still leaving the thread group. pidfd readiness follows
            // group exit. Wait briefly WITHOUT signaling or relaxing ownership.
            return tokio::time::timeout(Duration::from_secs(1), async {
                while !exited()? {
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }
                Ok(false)
            })
            .await
            .context("Supervisor command line empty but exit unconfirmed; refusing signal")?;
        }
        bail!("Supervisor ownership token mismatch in nonempty command line; refusing signal")
    }
    pub async fn cleanup(expected: &Identity) -> Result<()> {
        if expected.pid <= 1 || expected.token.is_empty() || expected.token.len() > 80 {
            bail!("Invalid supervisor ownership identity");
        }
        let fd = match pidfd(expected.pid) {
            Ok(fd) => fd,
            Err(e)
                if e.downcast_ref::<std::io::Error>()
                    .is_some_and(|e| e.raw_os_error() == Some(libc::ESRCH)) =>
            {
                return Ok(())
            }
            Err(e) => return Err(e),
        };
        if exited(&fd)? {
            return Ok(());
        }
        let actual = match identity(expected.pid, &expected.token) {
            Ok(i) => i,
            Err(_) if exited(&fd)? => return Ok(()),
            Err(e) => return Err(e),
        };
        if &actual != expected {
            return Ok(());
        } // original process is gone; never signal a reused PID
        let cmdline = match std::fs::read(format!("/proc/{}/cmdline", expected.pid)) {
            Ok(data) => data,
            Err(_) if exited(&fd)? => return Ok(()),
            Err(e) => return Err(e.into()),
        };
        if !token_owner(&cmdline, &expected.token, || exited(&fd)).await? {
            return Ok(());
        }
        let sent = unsafe {
            libc::syscall(
                libc::SYS_pidfd_send_signal,
                fd.as_raw_fd(),
                libc::SIGTERM,
                std::ptr::null::<libc::siginfo_t>(),
                0u32,
            )
        };
        if sent < 0 {
            let error = std::io::Error::last_os_error();
            if !exited(&fd)? {
                return Err(error.into());
            }
        }
        tokio::time::timeout(Duration::from_secs(10), async {
            while !exited(&fd)? {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
            Ok::<_, anyhow::Error>(())
        })
        .await
        .context("Direct process cleanup still pending; no restart/continuation permitted")??;
        Ok(())
    }

    #[cfg(test)]
    #[tokio::test]
    async fn token_check_distinguishes_exit_race_from_live_ownership_failure() {
        assert!(!token_owner(b"", "expected", || Ok(true)).await.unwrap());
        assert!(token_owner(b"", "expected", || Ok(false)).await.is_err());
        assert!(token_owner(b"other\0wrong\0", "expected", || Ok(false))
            .await
            .is_err());
        assert!(token_owner(b"program\0expected\0", "expected", || panic!(
            "matching token does not need fallback"
        ))
        .await
        .unwrap());
        assert!(
            token_owner(b"", "expected", || bail!("cannot establish exit"))
                .await
                .is_err()
        );
        let mut checks = 0;
        assert!(!token_owner(b"", "expected", || {
            checks += 1;
            Ok(checks >= 3)
        })
        .await
        .unwrap());
        assert_eq!(checks, 3); // cleared cmdline precedes confirmed group exit
    }

    static STOP: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    extern "C" fn stop(_: libc::c_int) {
        STOP.store(true, std::sync::atomic::Ordering::SeqCst);
    }
    fn stopping() -> bool {
        STOP.load(std::sync::atomic::Ordering::SeqCst)
    }

    /// Called in a dedicated process BEFORE constructing a Tokio runtime. Only
    /// this thread reaps children, preventing PID reuse between enumeration and kill.
    pub fn supervise(owner: u32, workspace: &Path, command: &[String]) -> Result<i32> {
        use std::io::{Read, Write};
        use std::process::{Command, Stdio};
        use std::sync::mpsc;
        if command.is_empty() || !Path::new(&command[0]).is_absolute() {
            bail!("Absolute tool executable required");
        }
        unsafe {
            let mut action: libc::sigaction = std::mem::zeroed();
            action.sa_sigaction = stop as *const () as usize;
            libc::sigemptyset(&mut action.sa_mask);
            for signal in [libc::SIGTERM, libc::SIGINT, libc::SIGHUP] {
                if libc::sigaction(signal, &action, std::ptr::null_mut()) != 0 {
                    return Err(std::io::Error::last_os_error().into());
                }
            }
            if libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0
                || libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM, 0, 0, 0) != 0
            {
                return Err(std::io::Error::last_os_error().into());
            }
            if libc::getppid() as u32 != owner || stopping() {
                return Ok(0);
            }
        }
        std::io::stdout().write_all(b"READY\n")?;
        std::io::stdout().flush()?;
        let mut start = [0u8; 6];
        if std::io::stdin().read_exact(&mut start).is_err() {
            return Ok(0);
        }
        if &start != b"START\n" || stopping() {
            return Ok(0);
        }
        // START arrives only after durable identity recording. No server/code ran before it.
        let mut child = Command::new(&command[0])
            .args(&command[1..])
            .current_dir(workspace)
            .env_clear()
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .env("HOME", workspace)
            .env("LANG", "C.UTF-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let pid = child.id() as libc::pid_t;
        let input = child.stdin.take().context("Missing child stdin")?;
        let output = child.stdout.take().context("Missing child stdout")?;
        let (tx, rx) = mpsc::channel();
        let end = tx.clone();
        std::thread::spawn(move || {
            let _ = std::io::copy(&mut std::io::stdin(), &mut { input });
            let _ = end.send(());
        });
        std::thread::spawn(move || {
            let _ = std::io::copy(&mut { output }, &mut std::io::stdout());
            let _ = tx.send(());
        });
        loop {
            if stopping() || rx.recv_timeout(Duration::from_millis(20)).is_ok() {
                break;
            }
            let mut status = 0;
            let result = unsafe { libc::waitpid(pid, &mut status, libc::WNOHANG) };
            if result == pid {
                break;
            }
            if result < 0 && std::io::Error::last_os_error().raw_os_error() != Some(libc::EINTR) {
                break;
            }
        }
        // Do not exit until every owned descendant is reaped. Killing immediate
        // children reparents their descendants here, even across setsid/double forks.
        // If cleanup is obstructed, stay alive: the runtime reports pending cleanup.
        loop {
            // A procfs problem must not make the supervisor exit and falsely
            // imply that cleanup succeeded. Keep ownership; host reports timeout.
            if let Ok(tasks) = std::fs::read_dir("/proc/self/task") {
                for task in tasks.flatten() {
                    if let Ok(ids) = std::fs::read_to_string(task.path().join("children")) {
                        for id in ids
                            .split_whitespace()
                            .filter_map(|s| s.parse::<libc::pid_t>().ok())
                        {
                            if id > 0 {
                                unsafe {
                                    libc::kill(id, libc::SIGKILL);
                                }
                            }
                        }
                    }
                }
            }
            loop {
                let result = unsafe { libc::waitpid(-1, std::ptr::null_mut(), libc::WNOHANG) };
                if result > 0 {
                    continue;
                }
                if result < 0
                    && std::io::Error::last_os_error().raw_os_error() == Some(libc::ECHILD)
                {
                    return Ok(0);
                }
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

pub fn preflight() -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        macos::identity(std::process::id(), "")?;
        Ok(())
    }
    #[cfg(windows)]
    {
        let _ = crate::platform::windows::process(std::process::id())?;
        Ok(())
    }
    #[cfg(target_os = "linux")]
    {
        linux::pidfd(std::process::id())?;
        linux::identity(std::process::id(), "")?;
        std::fs::read_to_string("/proc/thread-self/children")
            .context("Linux procfs child enumeration is required for direct cleanup")?;
        Ok(())
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    {
        bail!("Host execution is supported on Linux and Windows")
    }
}
pub fn identity(pid: u32, token: &str) -> Result<Identity> {
    #[cfg(target_os = "macos")]
    {
        macos::identity(pid, token)
    }
    #[cfg(windows)]
    {
        windows::identity(pid, token)
    }
    #[cfg(target_os = "linux")]
    {
        linux::identity(pid, token)
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    {
        let _ = (pid, token);
        bail!("Linux process identity unavailable")
    }
}
pub async fn cleanup(id: &Identity) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        macos::cleanup(id).await
    }
    #[cfg(windows)]
    {
        windows::cleanup(id).await
    }
    #[cfg(target_os = "linux")]
    {
        linux::cleanup(id).await
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    {
        let _ = id;
        bail!("Linux cleanup unavailable")
    }
}
pub fn supervise(owner: u32, workspace: &Path, command: &[String], token: &str) -> Result<i32> {
    #[cfg(target_os = "macos")]
    {
        macos::supervise(owner, workspace, command, token)
    }
    #[cfg(windows)]
    {
        windows::supervise(owner, workspace, command, token)
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    let _ = token;
    #[cfg(target_os = "linux")]
    {
        linux::supervise(owner, workspace, command)
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    {
        let _ = (owner, workspace, command);
        bail!("Supervisor is Linux-only")
    }
}
pub fn doctor() -> Value {
    let check = preflight();
    json!({"ready":check.is_ok(),"sandbox":"off","enforcement":"none","host_os":std::env::consts::OS,
        "warning":"UNSANDBOXED: tools have host-account file and network access. Approval mode is separate.",
        "error":check.err().map(|e|e.to_string()),"docker_required":false})
}

pub struct Mcp {
    child: tokio::process::Child,
    input: Option<tokio::process::ChildStdin>,
    output: BufReader<tokio::process::ChildStdout>,
    pub identity: Identity,
    sequence: u64,
    failed: bool,
}
impl Mcp {
    /// Spawn a paused supervisor. Caller MUST persist identity before start().
    pub async fn launch(runner: &Path, workspace: &Path, command: &[String]) -> Result<Self> {
        preflight()?;
        let token = crate::id("supervisor");
        let mut launch = std::process::Command::new(runner);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            launch.creation_flags(windows_sys::Win32::System::Threading::CREATE_NO_WINDOW);
        }
        crate::platform::clean_environment(&mut launch, workspace);
        #[cfg(target_os = "macos")]
        launch.env(
            "OPENWORKER_OWNER_START",
            macos::identity(std::process::id(), "")?
                .start_ticks
                .to_string(),
        );
        #[cfg(windows)]
        launch.env(
            "OPENWORKER_OWNER_START",
            crate::platform::windows::process(std::process::id())?
                .creation_time()?
                .to_string(),
        );
        let mut child = tokio::process::Command::from(launch)
            .args([
                "direct-supervisor",
                "--owner",
                &std::process::id().to_string(),
                "--token",
                &token,
                "--workspace",
            ])
            .arg(workspace)
            .arg("--")
            .args(command)
            .current_dir(workspace)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(false)
            .spawn()?;
        let id = identity(child.id().context("Missing supervisor PID")?, &token)?;
        let input = child.stdin.take();
        let mut output = BufReader::new(child.stdout.take().context("Missing supervisor stdout")?);
        let mut ready = [0u8; 6];
        tokio::time::timeout(Duration::from_secs(5), output.read_exact(&mut ready))
            .await
            .context("Supervisor readiness timed out")??;
        if &ready != b"READY\n" {
            bail!("Invalid supervisor readiness");
        }
        Ok(Self {
            child,
            input,
            output,
            identity: id,
            sequence: 0,
            failed: false,
        })
    }
    pub async fn start(&mut self) -> Result<()> {
        self.input
            .as_mut()
            .context("Supervisor closed")?
            .write_all(b"START\n")
            .await?;
        let init=self.request("initialize",json!({"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"openworker-native","version":"0.1.0"}})).await?;
        if !["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
            .contains(&init["protocolVersion"].as_str().unwrap_or(""))
        {
            bail!("Unsupported MCP protocol version");
        }
        self.send(json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}))
            .await
    }
    async fn send(&mut self, v: Value) -> Result<()> {
        let data = format!("{v}\n");
        if data.len() > crate::MAX_FRAME {
            bail!("MCP request exceeds frame limit");
        }
        self.input
            .as_mut()
            .context("MCP stdin closed")?
            .write_all(data.as_bytes())
            .await?;
        Ok(())
    }
    async fn line(&mut self) -> Result<Value> {
        let mut data = vec![];
        let n = (&mut self.output)
            .take((crate::MAX_FRAME + 1) as u64)
            .read_until(b'\n', &mut data)
            .await?;
        if n == 0 {
            bail!("Direct MCP disconnected");
        }
        if n > crate::MAX_FRAME || data.last() != Some(&b'\n') {
            bail!("Invalid/oversized MCP frame");
        }
        Ok(serde_json::from_slice(&data)?)
    }
    pub async fn request(&mut self, method: &str, params: Value) -> Result<Value> {
        self.request_timeout(method, params, Duration::from_secs(75))
            .await
    }
    pub async fn request_timeout(
        &mut self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value> {
        if self.failed {
            bail!("Direct MCP connection is no longer usable");
        }
        self.sequence += 1;
        let id = self.sequence;
        let result=tokio::time::timeout(timeout,async {
            self.send(json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})).await?;
            loop {
                let v=self.line().await?;
                if v.get("method").is_some() {
                    if v.get("id").is_some() {self.send(json!({"jsonrpc":"2.0","id":v["id"],"error":{"code":-32601,"message":"Server-initiated requests are not permitted"}})).await?;}
                    continue;
                }
                if v["id"]!=id {bail!("MCP response correlation mismatch");}
                if v.get("error").is_some() {bail!("MCP returned an error for {method}");}
                let result=v.get("result").context("MCP result missing")?;
                if serde_json::to_vec(result)?.len()>256000 {bail!("MCP result exceeds limit; execution may have occurred. Do not retry.");}
                return Ok(result.clone());
            }
        }).await.context("Direct MCP request timed out").and_then(|r|r);
        if result.is_err() {
            self.failed = true;
            self.close().await?;
        }
        result
    }
    pub async fn close(&mut self) -> Result<()> {
        self.failed = true;
        self.input.take();
        cleanup(&self.identity).await?;
        let status = tokio::time::timeout(Duration::from_secs(2), self.child.wait())
            .await
            .context("Supervisor reap pending")??;
        if !status.success() {
            bail!("Supervisor exited unexpectedly; descendant cleanup cannot be confirmed");
        }
        Ok(())
    }
}
