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
    let job = Job(Handle::new(unsafe {
        OpenJobObjectW(JOB_OBJECT_TERMINATE | JOB_OBJECT_QUERY, 0, name.as_ptr())
    })?);
    job.terminate()?;
    tokio::time::timeout(Duration::from_secs(10), async {
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
