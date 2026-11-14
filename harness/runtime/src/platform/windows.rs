//! Windows handles, private ACLs and owned-process jobs. Not a security sandbox.
use anyhow::{bail, Context, Result};
use std::{
    ffi::OsStr,
    os::windows::{ffi::OsStrExt, io::AsRawHandle},
    path::Path,
    ptr,
};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
    System::{JobObjects::*, Threading::*},
};

pub fn wide(s: impl AsRef<OsStr>) -> Vec<u16> {
    s.as_ref().encode_wide().chain(Some(0)).collect()
}
pub fn check(ok: i32) -> Result<()> {
    if ok == 0 {
        Err(std::io::Error::last_os_error().into())
    } else {
        Ok(())
    }
}
pub struct Handle(pub(crate) HANDLE);
// Owned kernel handles may be waited/queried across threads. No borrowed handles.
unsafe impl Send for Handle {}
unsafe impl Sync for Handle {}
impl Drop for Handle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
impl Handle {
    pub fn creation_time(&self) -> Result<u64> {
        creation(self.0)
    }
    pub fn exited(&self) -> Result<bool> {
        exited(self.0)
    }
    pub fn exit_code(&self) -> Result<u32> {
        let mut code = 0;
        unsafe {
            check(GetExitCodeProcess(self.0, &mut code))?;
        }
        Ok(code)
    }
    pub(crate) fn new(h: HANDLE) -> Result<Self> {
        if h.is_null() || h == INVALID_HANDLE_VALUE {
            Err(std::io::Error::last_os_error().into())
        } else {
            Ok(Self(h))
        }
    }
}
pub fn process(pid: u32) -> Result<Handle> {
    Handle::new(unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, 0, pid) })
}
fn creation(h: HANDLE) -> Result<u64> {
    let mut t = [FILETIME::default(); 4];
    unsafe {
        check(GetProcessTimes(
            h, &mut t[0], &mut t[1], &mut t[2], &mut t[3],
        ))?;
    }
    Ok((u64::from(t[0].dwHighDateTime) << 32) | u64::from(t[0].dwLowDateTime))
}
fn exited(h: HANDLE) -> Result<bool> {
    match unsafe { WaitForSingleObject(h, 0) } {
        WAIT_OBJECT_0 => Ok(true),
        WAIT_TIMEOUT => Ok(false),
        _ => Err(std::io::Error::last_os_error().into()),
    }
}
pub fn private_directory(path: &Path) -> Result<()> {
    unsafe {
        let mut token = ptr::null_mut();
        check(OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_QUERY,
            &mut token,
        ))?;
        let token = Handle::new(token)?;
        let mut size = 0;
        GetTokenInformation(token.0, TokenUser, ptr::null_mut(), 0, &mut size);
        let mut buf = vec![0usize; (size as usize).div_ceil(size_of::<usize>())];
        check(GetTokenInformation(
            token.0,
            TokenUser,
            buf.as_mut_ptr().cast(),
            size,
            &mut size,
        ))?;
        let user = &*buf.as_ptr().cast::<TOKEN_USER>();
        let mut sid = ptr::null_mut();
        check(ConvertSidToStringSidW(user.User.Sid, &mut sid))?;
        let mut len = 0;
        while *sid.add(len) != 0 {
            len += 1;
        }
        let sid_text = String::from_utf16(std::slice::from_raw_parts(sid, len));
        LocalFree(sid.cast());
        let sddl = wide(format!("D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{})", sid_text?));
        let mut sd = ptr::null_mut();
        check(ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            SDDL_REVISION_1,
            &mut sd,
            ptr::null_mut(),
        ))?;
        let result = check(SetFileSecurityW(
            wide(path).as_ptr(),
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            sd,
        ));
        LocalFree(sd);
        result
    }
}
pub fn file_identity(path: &Path) -> Result<(u64, u64, u64, u64, u64)> {
    use std::os::windows::fs::OpenOptionsExt;
    let f = std::fs::OpenOptions::new()
        .access_mode(FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    unsafe {
        check(GetFileInformationByHandle(f.as_raw_handle(), &mut info))?;
    }
    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        bail!("Reparse point rejected");
    }
    let value = |t: FILETIME| (u64::from(t.dwHighDateTime) << 32) | u64::from(t.dwLowDateTime);
    Ok((
        u64::from(info.dwVolumeSerialNumber),
        (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
        (u64::from(info.nFileSizeHigh) << 32) | u64::from(info.nFileSizeLow),
        value(info.ftCreationTime),
        value(info.ftLastWriteTime),
    ))
}
pub fn job_name(token: &str) -> Result<Vec<u16>> {
    if !token.starts_with("supervisor_") || !crate::bundle::safe_name(token) {
        bail!("Invalid job token");
    }
    Ok(wide(format!("Local\\OpenWorker-{token}")))
}
pub struct Job(pub Handle);
impl Job {
    pub fn new(token: Option<&str>) -> Result<Self> {
        let name = token.map(job_name).transpose()?;
        let handle = Handle::new(unsafe {
            CreateJobObjectW(
                ptr::null(),
                name.as_ref().map_or(ptr::null(), |n| n.as_ptr()),
            )
        })?;
        if name.is_some() && unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
            bail!("Job token already exists");
        }
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        unsafe {
            check(SetInformationJobObject(
                handle.0,
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of_val(&limits) as u32,
            ))?;
        }
        Ok(Self(handle))
    }
    pub fn terminate(&self) -> Result<()> {
        unsafe { check(TerminateJobObject(self.0 .0, 0)) }
    }
    pub fn active(&self) -> Result<u32> {
        let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        unsafe {
            check(QueryInformationJobObject(
                self.0 .0,
                JobObjectBasicAccountingInformation,
                (&mut info as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                size_of_val(&info) as u32,
                ptr::null_mut(),
            ))?;
        }
        Ok(info.ActiveProcesses)
    }
    pub async fn wait_empty(&self) -> Result<()> {
        tokio::time::timeout(std::time::Duration::from_secs(10), async {
            while self.active()? != 0 || !self.0.exited()? {
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
            Ok::<_, anyhow::Error>(())
        })
        .await
        .context("Owned job cleanup pending")?
    }
}
/// Windows CRT argv quoting, never a shell command line.
pub fn quote(arg: &str) -> Result<String> {
    if arg.contains('\0') {
        bail!("NUL in argument");
    }
    let mut out = String::from("\"");
    let mut slashes = 0;
    for c in arg.chars() {
        if c == '\\' {
            slashes += 1;
            continue;
        }
        out.extend(std::iter::repeat_n(
            '\\',
            if c == '\"' { 2 * slashes + 1 } else { slashes },
        ));
        slashes = 0;
        out.push(c);
    }
    out.extend(std::iter::repeat_n('\\', 2 * slashes));
    out.push('"');
    Ok(out)
}
/// Attach to the Job atomically at CreateProcess, before any child code can run.
/// Explicit inherited-handle list prevents accidentally inheriting the Job handle.
pub fn spawn_in_job(
    job: &Job,
    workspace: &Path,
    command: &[String],
    handles: [HANDLE; 3],
) -> Result<Handle> {
    if command.is_empty() || !Path::new(&command[0]).is_absolute() {
        bail!("Absolute executable required");
    }
    let exe = wide(&command[0]);
    let mut argv = wide(
        command
            .iter()
            .map(|s| quote(s))
            .collect::<Result<Vec<_>>>()?
            .join(" "),
    );
    let cwd = wide(workspace);
    let mut environment = Vec::new();
    let mut vars = std::collections::BTreeMap::new();
    for name in ["SystemRoot", "WINDIR", "SystemDrive"] {
        if let Ok(value) = std::env::var(name) {
            vars.insert(name, value);
        }
    }
    if let Some(root) = vars.get("SystemRoot") {
        vars.insert("PATH", format!("{root}\\System32"));
    }
    for name in ["HOME", "USERPROFILE", "TEMP", "TMP"] {
        vars.insert(
            name,
            workspace
                .to_str()
                .context("UTF-8 workspace required")?
                .into(),
        );
    }
    for (name, value) in vars {
        environment.extend(wide(format!("{name}={value}")));
    }
    environment.push(0);
    let mut inherited = Vec::new();
    for h in handles {
        let mut copy = ptr::null_mut();
        unsafe {
            check(DuplicateHandle(
                GetCurrentProcess(),
                h,
                GetCurrentProcess(),
                &mut copy,
                0,
                1,
                DUPLICATE_SAME_ACCESS,
            ))?;
        }
        inherited.push(Handle::new(copy)?);
    }
    unsafe {
        let mut size = 0;
        InitializeProcThreadAttributeList(ptr::null_mut(), 2, 0, &mut size);
        let mut allocation = vec![0usize; size.div_ceil(size_of::<usize>())];
        let attrs = allocation.as_mut_ptr().cast();
        check(InitializeProcThreadAttributeList(attrs, 2, 0, &mut size))?;
        struct Attributes(LPPROC_THREAD_ATTRIBUTE_LIST);
        impl Drop for Attributes {
            fn drop(&mut self) {
                unsafe { DeleteProcThreadAttributeList(self.0) }
            }
        }
        let _attrs = Attributes(attrs);
        let handle_list = [inherited[0].0, inherited[1].0, inherited[2].0];
        check(UpdateProcThreadAttribute(
            attrs,
            0,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
            handle_list.as_ptr().cast(),
            size_of_val(&handle_list),
            ptr::null_mut(),
            ptr::null(),
        ))?;
        let jobs = [job.0 .0];
        check(UpdateProcThreadAttribute(
            attrs,
            0,
            PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
            jobs.as_ptr().cast(),
            size_of_val(&jobs),
            ptr::null_mut(),
            ptr::null(),
        ))?;
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = handle_list[0];
        startup.StartupInfo.hStdOutput = handle_list[1];
        startup.StartupInfo.hStdError = handle_list[2];
        startup.lpAttributeList = attrs;
        let mut process = PROCESS_INFORMATION::default();
        check(CreateProcessW(
            exe.as_ptr(),
            argv.as_mut_ptr(),
            ptr::null(),
            ptr::null(),
            1,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
            environment.as_ptr().cast(),
            cwd.as_ptr(),
            &startup.StartupInfo,
            &mut process,
        ))
        .context("Spawn in owned Job")?;
        let _thread = Handle::new(process.hThread)?;
        Handle::new(process.hProcess)
    }
}

pub async fn shell(workspace: &Path, command: &str) -> Result<serde_json::Value> {
    use std::{io::Read, os::windows::io::FromRawHandle, time::Duration};
    fn pipe() -> Result<(std::fs::File, std::fs::File)> {
        let (mut read, mut write) = (ptr::null_mut(), ptr::null_mut());
        unsafe {
            check(windows_sys::Win32::System::Pipes::CreatePipe(
                &mut read,
                &mut write,
                ptr::null(),
                0,
            ))?;
            Ok((
                std::fs::File::from_raw_handle(read),
                std::fs::File::from_raw_handle(write),
            ))
        }
    }
    fn read(mut file: std::fs::File) -> Result<Vec<u8>> {
        let mut data = Vec::new();
        (&mut file).take(64001).read_to_end(&mut data)?;
        if data.len() > 64000 {
            bail!("Tool output exceeds 64 KB");
        }
        Ok(data)
    }
    let shell =
        std::path::PathBuf::from(std::env::var_os("SystemRoot").context("SystemRoot missing")?)
            .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let job = Job::new(None)?;
    let (out, out_write) = pipe()?;
    let (err, err_write) = pipe()?;
    let input = std::fs::File::open("NUL")?;
    let child = spawn_in_job(
        &job,
        workspace,
        &[
            shell.to_str().context("Shell path UTF-8")?.into(),
            "-NoLogo".into(),
            "-NoProfile".into(),
            "-NonInteractive".into(),
            "-Command".into(),
            command.into(),
        ],
        [
            input.as_raw_handle(),
            out_write.as_raw_handle(),
            err_write.as_raw_handle(),
        ],
    )?;
    drop(out_write);
    drop(err_write);
    let stdout = tokio::task::spawn_blocking(move || read(out));
    let stderr = tokio::task::spawn_blocking(move || read(err));
    let result=tokio::time::timeout(Duration::from_secs(60),async {
        // Reap background descendants when the primary shell ends, so inherited
        // pipe handles cannot keep readers blocked after command completion.
        while !child.exited()? {tokio::time::sleep(Duration::from_millis(10)).await;}
        let code=child.exit_code()?;
        job.terminate()?;
        let out=stdout.await??;let err=stderr.await??;
        Ok::<_,anyhow::Error>(serde_json::json!({"stdout":String::from_utf8_lossy(&out),"stderr":String::from_utf8_lossy(&err),"exit_code":code}))
    }).await;
    job.terminate()?;
    job.wait_empty().await?;
    result.context("Windows command timed out; effects may have occurred")?
}
