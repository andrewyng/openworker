//! Explicit sandbox-off profile for trusted MCP executables on macOS.
//! A private control socket avoids signaling a PID that may have been recycled.
//! Process-group cleanup is lifecycle management, not containment: deliberately
//! detached/daemonized processes require an isolated runner, not this profile.
use super::*;
use std::{
    io::{Read, Write},
    os::unix::{
        fs::{MetadataExt, PermissionsExt},
        net::UnixListener,
    },
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
};
static STOP: AtomicBool = AtomicBool::new(false);
extern "C" fn stop(_: libc::c_int) {
    STOP.store(true, Ordering::SeqCst);
}

pub fn identity(pid: u32, token: &str) -> Result<Identity> {
    anyhow::ensure!(
        pid > 0 && pid <= i32::MAX as u32,
        "Invalid macOS process identity"
    );
    let mut info = std::mem::MaybeUninit::<libc::proc_bsdinfo>::zeroed();
    let size = std::mem::size_of::<libc::proc_bsdinfo>();
    let read = unsafe {
        libc::proc_pidinfo(
            pid as i32,
            libc::PROC_PIDTBSDINFO,
            0,
            info.as_mut_ptr().cast(),
            size as i32,
        )
    };
    if read != size as i32 {
        return Err(std::io::Error::last_os_error().into());
    }
    let info = unsafe { info.assume_init() };
    if info.pbi_status == libc::SZOMB {
        return Err(std::io::Error::from_raw_os_error(libc::ESRCH).into());
    }
    Ok(Identity {
        pid,
        boot_id: "darwin-unix-start-time-v1".into(),
        start_ticks: info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec,
        token: token.into(),
    })
}
fn socket_path(token: &str) -> Result<std::path::PathBuf> {
    anyhow::ensure!(
        token.starts_with("supervisor_")
            && token.len() == 43
            && token
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_'),
        "Invalid supervisor token"
    );
    // /private/tmp is canonical on macOS; do not rely on an untrusted TMPDIR.
    let uid = unsafe { libc::geteuid() };
    let root = std::path::PathBuf::from(format!("/private/tmp/owr-{uid}"));
    match std::fs::create_dir(&root) {
        Ok(()) => std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o700))?,
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e.into()),
    }
    let meta = root.symlink_metadata()?;
    anyhow::ensure!(
        meta.is_dir()
            && !meta.file_type().is_symlink()
            && meta.uid() == uid
            && meta.mode() & 0o077 == 0,
        "Untrusted supervisor socket directory"
    );
    Ok(root.join(format!("{token}.sock")))
}
pub async fn cleanup(expected: &Identity) -> Result<()> {
    anyhow::ensure!(expected.pid > 1, "Invalid supervisor identity");
    let still_owned = || -> Result<bool> {
        match identity(expected.pid, &expected.token) {
            Ok(actual) => Ok(&actual == expected),
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|e| e.raw_os_error() == Some(libc::ESRCH)) =>
            {
                Ok(false)
            }
            Err(error) => Err(error),
        }
    };
    if !still_owned()? {
        return Ok(());
    }
    let path = socket_path(&expected.token)?;
    let request = async {
        let mut socket = tokio::net::UnixStream::connect(&path).await?;
        socket
            .write_all(format!("STOP {}\n", expected.token).as_bytes())
            .await?;
        while still_owned()? {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        Ok::<_, anyhow::Error>(())
    };
    let result = tokio::time::timeout(Duration::from_secs(10), request).await;
    // A normal exit can win the race with connecting to its control socket.
    if !still_owned()? {
        let _ = std::fs::remove_file(path);
        return Ok(());
    }
    result.context("macOS supervisor cleanup timed out; ownership retained")??;
    Ok(())
}
pub fn supervise(owner: u32, workspace: &Path, command: &[String], token: &str) -> Result<i32> {
    anyhow::ensure!(!command.is_empty(), "Supervisor requires an executable");
    let expected: u64 = std::env::var("OPENWORKER_OWNER_START")?.parse()?;
    let owner_alive = || identity(owner, "").is_ok_and(|id| id.start_ticks == expected);
    if unsafe { libc::getppid() } as u32 != owner || !owner_alive() {
        return Ok(0);
    }
    // Only this dedicated helper changes its session/process group, never the app.
    if unsafe { libc::setsid() } < 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    unsafe {
        libc::signal(libc::SIGTERM, stop as *const () as libc::sighandler_t);
        libc::signal(libc::SIGINT, stop as *const () as libc::sighandler_t);
    }
    let path = socket_path(token)?;
    let listener = UnixListener::bind(&path)?;
    listener.set_nonblocking(true)?;
    let stopping = || -> bool {
        if STOP.load(Ordering::SeqCst) || !owner_alive() {
            return true;
        }
        if let Ok((mut socket, _)) = listener.accept() {
            let _ = socket.set_read_timeout(Some(Duration::from_millis(100)));
            let mut bytes = [0u8; 100];
            if let Ok(n) = socket.read(&mut bytes) {
                return &bytes[..n] == format!("STOP {token}\n").as_bytes();
            }
        }
        false
    };
    std::io::stdout().write_all(b"READY\n")?;
    std::io::stdout().flush()?;
    let (ready, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut bytes = [0; 6];
        let ok = std::io::stdin().read_exact(&mut bytes).is_ok() && &bytes == b"START\n";
        let _ = ready.send(ok);
    });
    loop {
        if stopping() {
            let _ = std::fs::remove_file(&path);
            return Ok(0);
        }
        match rx.recv_timeout(Duration::from_millis(20)) {
            Ok(true) => break,
            Ok(false) | Err(mpsc::RecvTimeoutError::Disconnected) => {
                let _ = std::fs::remove_file(&path);
                return Ok(0);
            }
            Err(_) => {}
        }
    }
    let mut launch = Command::new(&command[0]);
    use std::os::unix::process::CommandExt;
    launch.process_group(0);
    crate::platform::clean_environment(&mut launch, workspace);
    let mut child = launch
        .args(&command[1..])
        .current_dir(workspace)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let mut input = child.stdin.take().context("Missing MCP input")?;
    let mut output = child.stdout.take().context("Missing MCP output")?;
    let (end, ended) = mpsc::channel();
    let end2 = end.clone();
    std::thread::spawn(move || {
        let _ = std::io::copy(&mut std::io::stdin(), &mut input);
        let _ = end.send(());
    });
    std::thread::spawn(move || {
        let _ = std::io::copy(&mut output, &mut std::io::stdout());
        let _ = end2.send(());
    });
    loop {
        if stopping()
            || ended.recv_timeout(Duration::from_millis(20)).is_ok()
            || identity(child.id(), "").is_err()
        {
            break;
        }
    }
    // Do not reap the child until cleanup finishes: its allocated PID reserves
    // this group identity even if the group leader has already become a zombie.
    loop {
        unsafe {
            libc::killpg(child.id() as i32, libc::SIGKILL);
        }
        let mut pids = [0i32; 4096];
        let count = unsafe {
            libc::proc_listpgrppids(
                child.id() as i32,
                pids.as_mut_ptr().cast(),
                std::mem::size_of_val(&pids) as i32,
            )
        };
        if count >= 0
            && (count as usize) < pids.len()
            && pids[..count as usize].iter().all(|&pid| {
                identity(pid as u32, "").is_err_and(|e| {
                    e.downcast_ref::<std::io::Error>()
                        .is_some_and(|e| e.raw_os_error() == Some(libc::ESRCH))
                })
            })
        {
            break;
        }
        // If enumeration/termination fails, retain ownership and let the caller
        // report cleanup pending instead of claiming success.
        std::thread::sleep(Duration::from_millis(20));
    }
    child.wait()?;
    let _ = std::fs::remove_file(&path);
    Ok(0)
}
