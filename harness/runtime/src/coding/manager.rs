//! Sandbox-local process ownership. Connecting may be retried before delivery;
//! a tool request is sent once. A lost launch reply is unknown, never replayed.
use super::processes::Processes;
use anyhow::{bail, ensure, Context, Result};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs::OpenOptions,
    os::unix::{
        fs::{FileTypeExt, OpenOptionsExt, PermissionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::{UnixListener, UnixStream},
};

fn directory(root: &Path) -> Result<PathBuf> {
    let dir = root.join(".openworker-processes");
    match std::fs::symlink_metadata(&dir) {
        Ok(m) => ensure!(
            m.is_dir() && !m.file_type().is_symlink(),
            "Process state must be a real directory"
        ),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => match std::fs::create_dir(&dir) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(e.into()),
        },
        Err(e) => return Err(e.into()),
    }
    ensure!(
        std::fs::symlink_metadata(&dir)?.is_dir(),
        "Invalid process directory"
    );
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))?;
    Ok(dir)
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    name: String,
    args: Value,
    launch_id: Option<String>,
}
async fn line<R: tokio::io::AsyncRead + Unpin>(r: &mut BufReader<R>) -> Result<Value> {
    let mut bytes = Vec::new();
    let n = tokio::time::timeout(
        Duration::from_secs(15),
        (&mut *r).take(128001).read_until(b'\n', &mut bytes),
    )
    .await??;
    ensure!(
        n > 0 && n <= 128000 && bytes.last() == Some(&b'\n'),
        "Process-manager frame incomplete or too large"
    );
    Ok(serde_json::from_slice(&bytes)?)
}
async fn send<W: tokio::io::AsyncWrite + Unpin>(w: &mut W, v: &Value) -> Result<()> {
    let mut b = serde_json::to_vec(v)?;
    b.push(b'\n');
    tokio::time::timeout(Duration::from_secs(5), w.write_all(&b)).await??;
    Ok(())
}
/// Invoked only by the sandbox's MCP adapter, not by model-selected executables.
pub async fn call(root: &Path, name: &str, args: &Value) -> Result<Value> {
    let root = root.canonicalize()?;
    let dir = directory(&root)?;
    let path = dir.join("manager.sock");
    let launch = matches!(name, "run_shell" | "process_start");
    let mut stream = match UnixStream::connect(&path).await {
        Ok(s) => s,
        Err(_) if !launch => {
            return Ok(
                json!({"isError":true,"executed":false,"code":"process_manager_unavailable","message":"The owning manager is unavailable. Old processes may have stopped or become unknown. Check the endpoint before proposing a fresh launch; do not replay a launch automatically."}),
            )
        }
        Err(_) => {
            let mut child = Command::new(std::env::current_exe()?);
            child
                .args(["coding-process-manager", "--workspace"])
                .arg(&root)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            // Inherit the already sandboxed process's restrictions. Detach only
            // lifetime/stdio, never filesystem, network policy or user identity.
            unsafe {
                child.pre_exec(|| {
                    if libc::setsid() < 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    Ok(())
                });
            }
            child.env_clear();
            for key in [
                "PATH",
                "HOME",
                "TMPDIR",
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
                "NODE_EXTRA_CA_CERTS",
                "NODE_USE_ENV_PROXY",
                "GIT_SSL_CAINFO",
                "CURL_CA_BUNDLE",
                "REQUESTS_CA_BUNDLE",
            ] {
                if let Some(v) = std::env::var_os(key) {
                    child.env(key, v);
                }
            }
            let mut child = child.spawn()?;
            std::thread::spawn(move || {
                let _ = child.wait();
            });
            let start = tokio::time::Instant::now();
            loop {
                match UnixStream::connect(&path).await {
                    Ok(s) => break s,
                    Err(_) if start.elapsed() < Duration::from_secs(4) => {
                        tokio::time::sleep(Duration::from_millis(30)).await
                    }
                    Err(_) => bail!("Process manager could not start; no command was sent"),
                }
            }
        }
    };
    ensure!(
        stream.peer_cred()?.uid() == unsafe { libc::geteuid() },
        "Process-manager peer identity mismatch"
    );
    let (r, w) = stream.split();
    let mut r = BufReader::new(r);
    let mut w = w;
    let hello = line(&mut r).await?;
    let generation = hello["generation"]
        .as_str()
        .context("Missing process generation")?;
    ensure!(
        generation.len() == 32 && generation.bytes().all(|c| c.is_ascii_hexdigit()),
        "Invalid manager identity"
    );
    let prefix = format!("process_{generation}_");
    if !launch
        && !args["process_id"]
            .as_str()
            .is_some_and(|id| id.starts_with(&prefix))
    {
        return Ok(
            json!({"isError":true,"executed":false,"code":"stale_process_handle","message":"Handle belongs to another workspace/manager generation. Do not use a raw PID or assume the old process survived."}),
        );
    }
    let id = launch.then(|| format!("{prefix}{}", uuid::Uuid::new_v4().simple()));
    let request = serde_json::to_value(Request {
        name: name.into(),
        args: args.clone(),
        launch_id: id.clone(),
    })?;
    // No retry after this point: bytes may have reached the owning manager.
    let reply = async {
        send(&mut w, &request).await?;
        line(&mut r).await
    }
    .await;
    match reply {
        Ok(v) => Ok(v),
        Err(_) => Ok(
            json!({"isError":true,"executed":null,"code":"process_outcome_unknown","process_id":id.or_else(||args["process_id"].as_str().map(str::to_owned)),"message":"Manager reply was lost; command may have executed. Use the supplied handle to check status/logs and inspect the endpoint before considering another launch. Do not retry automatically."}),
        ),
    }
}
pub async fn serve(root: &Path) -> Result<()> {
    let root = root.canonicalize()?;
    let dir = directory(&root)?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(dir.join("manager.lock"))?;
    if lock.try_lock_exclusive().is_err() {
        return Ok(());
    }
    let socket = dir.join("manager.sock");
    if let Ok(meta) = std::fs::symlink_metadata(&socket) {
        ensure!(
            meta.file_type().is_socket(),
            "Refusing to replace non-socket manager state"
        );
        std::fs::remove_file(&socket)?;
    }
    let listener = UnixListener::bind(&socket)?;
    std::fs::set_permissions(&socket, std::fs::Permissions::from_mode(0o600))?;
    let generation = uuid::Uuid::new_v4().simple().to_string();
    let mut processes = Processes::default();
    let born = tokio::time::Instant::now();
    let mut last = born;
    let result:Result<()>=async {
        loop {
            let (mut stream,_)=tokio::select! {
                s=listener.accept()=>s?,
                _=tokio::time::sleep(Duration::from_secs(30))=>{
                    if !processes.active()&&last.elapsed()>Duration::from_secs(900){break}continue
                }
            };
            if stream.peer_cred()?.uid()!=unsafe{libc::geteuid()}{continue}
            let (r,w)=stream.split();let mut r=BufReader::new(r);let mut w=w;
            if send(&mut w,&json!({"generation":generation})).await.is_err(){continue}
            let request=match line(&mut r).await.and_then(|v|Ok(serde_json::from_value::<Request>(v)?)){Ok(v)=>v,Err(_)=>continue};
            let prefix=format!("process_{generation}_");
            let valid=if matches!(request.name.as_str(),"run_shell"|"process_start") {
                request.launch_id.as_ref().is_some_and(|id|id.len()==prefix.len()+32&&id.starts_with(&prefix)&&id[prefix.len()..].bytes().all(|c|c.is_ascii_hexdigit()))
            }else{matches!(request.name.as_str(),"process_status"|"process_logs"|"process_stop")&&request.args["process_id"].as_str().is_some_and(|id|id.starts_with(&prefix))};
            if !valid{let _=send(&mut w,&json!({"isError":true,"executed":false,"code":"invalid_process_request"})).await;continue}
            last=tokio::time::Instant::now();
            let reply=match processes.call_as(&root,&request.name,&request.args,request.launch_id.as_deref()).await {Ok(v)=>v,Err(_)=>json!({"isError":true,"executed":false,"code":"process_request_rejected","message":"Invalid or unknown process request. Do not repeat a launch whose outcome is unknown."})};
            let _=send(&mut w,&reply).await;
        }
        Ok(())
    }.await;
    processes.shutdown().await;
    let _ = std::fs::remove_file(&socket);
    drop(lock);
    result
}
