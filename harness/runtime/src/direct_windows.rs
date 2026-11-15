use super::Identity;
use crate::platform::windows::*;
use anyhow::{bail, Context, Result};
use std::{
    io::Write,
    os::windows::io::AsRawHandle,
    path::Path,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::*,
    Storage::FileSystem::ReadFile,
    System::{
        Console::*,
        JobObjects::*,
        SystemServices::{JOB_OBJECT_QUERY, JOB_OBJECT_TERMINATE},
    },
};

pub fn identity(pid: u32, token: &str) -> Result<Identity> {
    let p = process(pid)?;
    Ok(Identity {
        pid,
        boot_id: "windows-process-creation-time-v1".into(),
        start_ticks: p.creation_time()?,
        token: token.into(),
    })
}
pub async fn cleanup(id: &Identity) -> Result<()> {
    cleanup_with_timeout(id, Duration::from_secs(10)).await
}
async fn cleanup_with_timeout(id: &Identity, timeout: Duration) -> Result<()> {
    let p = match process(id.pid) {
        Ok(p) => p,
        Err(e)
            if e.downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.raw_os_error() == Some(ERROR_INVALID_PARAMETER as i32)) =>
        {
            return Ok(())
        }
        Err(e) => return Err(e),
    };
    if p.exited()? || p.creation_time()? != id.start_ticks {
        return Ok(());
    }
    if id.boot_id != "windows-process-creation-time-v1" {
        bail!("Wrong platform process identity");
    }
    let name = job_name(&id.token)?;
    let job = Handle::new(unsafe {
        OpenJobObjectW(JOB_OBJECT_TERMINATE | JOB_OBJECT_QUERY, 0, name.as_ptr())
    });
    let job = match job {
        Ok(handle) => Job(handle),
        Err(error)
            if error
                .downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.raw_os_error() == Some(ERROR_FILE_NOT_FOUND as i32)) =>
        {
            // EOF can make the supervisor drop its kill-on-close job before
            // its process is signaled. A missing name alone is NOT proof of
            // cleanup: wait on the original, creation-time-verified handle.
            return tokio::time::timeout(timeout, async {
                while !p.exited()? {
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
                Ok::<_, anyhow::Error>(())
            })
            .await
            .context("Windows supervisor exit pending after job disappeared")?;
        }
        Err(error) => return Err(error),
    };
    job.terminate()?;
    tokio::time::timeout(timeout, async {
        while job.active()? != 0 || !p.exited()? {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        Ok::<_, anyhow::Error>(())
    })
    .await
    .context("Windows job cleanup pending")??;
    Ok(())
}
pub fn supervise(owner: u32, workspace: &Path, command: &[String], token: &str) -> Result<i32> {
    let parent = process(owner)?;
    let expected = std::env::var("OPENWORKER_OWNER_START")?.parse::<u64>()?;
    if parent.creation_time()? != expected || parent.exited()? {
        bail!("Supervisor parent identity changed");
    }
    std::env::remove_var("OPENWORKER_OWNER_START");
    let job = Job::new(Some(token))?;
    std::io::stdout().write_all(b"READY\n")?;
    std::io::stdout().flush()?;
    let mut start = [0; 6];
    let mut received = 0;
    // The child inherits this pipe directly. Buffered Stdin can prefetch the
    // following MCP initialize frame and strand it in the supervisor's buffer.
    // Read precisely the handoff marker from the kernel, leaving later bytes.
    while received < start.len() {
        let mut count = 0;
        let read = unsafe {
            ReadFile(
                GetStdHandle(STD_INPUT_HANDLE),
                start[received..].as_mut_ptr(),
                (start.len() - received) as u32,
                &mut count,
                std::ptr::null_mut(),
            )
        };
        if read == 0 || count == 0 {
            return Ok(0);
        }
        received += count as usize;
    }
    if &start != b"START\n" || parent.exited()? {
        return Ok(0);
    }
    let null = std::fs::OpenOptions::new().write(true).open("NUL")?;
    let child = spawn_in_job(&job, workspace, command, unsafe {
        [
            GetStdHandle(STD_INPUT_HANDLE),
            GetStdHandle(STD_OUTPUT_HANDLE),
            null.as_raw_handle(),
        ]
    })?;
    while !parent.exited()? && !child.exited()? {
        std::thread::sleep(Duration::from_millis(10));
    }
    job.terminate()?;
    let end = Instant::now() + Duration::from_secs(10);
    while job.active()? != 0 || !job.0.exited()? {
        if Instant::now() > end {
            bail!("Owned Windows descendants still running");
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exit_fixture() {
        let Ok(token) = std::env::var("OW_TEST_CLOSED_JOB_TOKEN") else {
            return;
        };
        // Deterministically hold the gap between dropping the named job and
        // exiting, instead of relying on a scheduler race in the regression.
        drop(Job::new(Some(&token)).unwrap());
        println!("JOB_CLOSED");
        std::io::stdout().flush().unwrap();
        let mut input = String::new();
        std::io::stdin().read_line(&mut input).unwrap();
    }

    #[tokio::test]
    async fn missing_job_requires_verified_supervisor_exit() {
        use tokio::io::{AsyncBufReadExt, BufReader};
        let token = crate::id("supervisor");
        let mut child = tokio::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "direct::windows::tests::exit_fixture",
                "--nocapture",
            ])
            .env("OW_TEST_CLOSED_JOB_TOKEN", &token)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let input = child.stdin.take().unwrap();
        let mut lines = BufReader::new(child.stdout.take().unwrap()).lines();
        tokio::time::timeout(Duration::from_secs(5), async {
            while !lines
                .next_line()
                .await
                .unwrap()
                .unwrap()
                .contains("JOB_CLOSED")
            {}
        })
        .await
        .unwrap();
        let id = identity(child.id().unwrap(), &token).unwrap();
        let owned_process = process(id.pid).unwrap();
        assert!(cleanup_with_timeout(&id, Duration::from_millis(30))
            .await
            .is_err());
        assert!(!owned_process.exited().unwrap());
        let release = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(30)).await;
            drop(input);
        });
        cleanup(&id).await.unwrap();
        assert!(owned_process.exited().unwrap());
        assert!(child.wait().await.unwrap().success());
        release.await.unwrap();
    }
}
