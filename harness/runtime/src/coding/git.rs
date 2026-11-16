use anyhow::{ensure, Context, Result};
use serde_json::{json, Value};
use std::{path::Path, process::Stdio, time::Duration};
use tokio::io::AsyncReadExt;
pub(crate) fn command(root: &Path, args: &[String]) -> Result<tokio::process::Command> {
    let null = if cfg!(windows) { "NUL" } else { "/dev/null" };
    let hooks = format!("core.hooksPath={null}");
    // Never resolve a repository-provided git/helper through the app's PATH.
    #[cfg(not(windows))]
    let executable = ["/usr/bin/git", "/usr/local/bin/git"]
        .into_iter()
        .map(std::path::PathBuf::from)
        .find(|p| p.is_file())
        .context("Git must be installed outside the project")?;
    #[cfg(windows)]
    let executable = [
        std::env::var_os("ProgramFiles")
            .map(|p| std::path::PathBuf::from(p).join("Git/cmd/git.exe")),
        std::env::var_os("LOCALAPPDATA")
            .map(|p| std::path::PathBuf::from(p).join("Programs/Git/cmd/git.exe")),
    ]
    .into_iter()
    .flatten()
    .find(|p| p.is_file() && !p.starts_with(root))
    .context("Install Git for Windows outside the project")?;
    let mut command = tokio::process::Command::new(executable);
    command
        .args([
            "--no-pager",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            hooks.as_str(),
            "-c",
            "color.ui=false",
            "-c",
            "submodule.recurse=false",
        ])
        .args(args)
        .current_dir(root)
        .env_clear()
        .env(
            "PATH",
            if cfg!(windows) {
                "C:\\Windows\\System32"
            } else {
                "/usr/local/bin:/usr/bin:/bin"
            },
        )
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", null)
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_OPTIONAL_LOCKS", "0")
        .envs(if cfg!(windows) {
            std::env::var_os("SystemRoot").map(|v| ("SystemRoot", v))
        } else {
            None
        });
    command
        .env("GIT_NO_REPLACE_OBJECTS", "1")
        .env("GIT_NO_LAZY_FETCH", "1");
    #[cfg(unix)]
    command.process_group(0);
    Ok(command)
}
/// Owns the process group across future cancellation as well as explicit errors.
/// A live/unreaped child PID cannot yet have been reused by another process.
pub(crate) struct GitChild(pub tokio::process::Child);
impl std::ops::Deref for GitChild {
    type Target = tokio::process::Child;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}
impl std::ops::DerefMut for GitChild {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.0
    }
}
impl Drop for GitChild {
    fn drop(&mut self) {
        #[cfg(unix)]
        if let Some(pid) = self.0.id().filter(|p| *p > 0 && *p <= i32::MAX as u32) {
            // SAFETY: command() creates this child's private process group.
            unsafe {
                libc::kill(-(pid as i32), libc::SIGKILL);
            }
        }
    }
}
/// Git helpers are spawned in their own process group on Unix. Reap the parent
/// and its pack/index children on cancellation, timeout or output-limit failure.
pub(crate) async fn stop(child: &mut tokio::process::Child) {
    #[cfg(unix)]
    if let Some(pid) = child.id().filter(|p| *p > 0 && *p <= i32::MAX as u32) {
        // SAFETY: command() configured process_group(0), so this positive child
        // PID names our spawned group's leader, never an arbitrary caller PID.
        unsafe {
            libc::kill(-(pid as i32), libc::SIGKILL);
        }
    }
    let _ = child.kill().await;
    let _ = child.wait().await;
}
pub(crate) async fn run(root: &Path, args: &[String]) -> Result<String> {
    run_bounded(root, args, 48000).await
}
pub(crate) async fn run_bounded(root: &Path, args: &[String], limit: usize) -> Result<String> {
    ensure!(
        limit > 0 && limit <= 2 * 1024 * 1024,
        "Invalid Git output limit"
    );
    let mut child = GitChild(
        command(root, args)?
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .context("Git must be installed in the tool environment")?,
    );
    let mut stdout = child.stdout.take().unwrap().take(limit as u64 + 1);
    let mut stderr = child.stderr.take().unwrap().take(4001);
    let mut out = vec![];
    let mut err = vec![];
    let result = tokio::time::timeout(Duration::from_secs(10), async {
        let (_, _, status) = tokio::try_join!(
            stdout.read_to_end(&mut out),
            stderr.read_to_end(&mut err),
            child.wait()
        )?;
        ensure!(
            out.len() <= limit && err.len() <= 4000,
            "Git output too large; narrow the path or count"
        );
        ensure!(
            status.success(),
            "Git inspection failed: {}",
            String::from_utf8_lossy(&err)
        );
        Ok::<_, anyhow::Error>(String::from_utf8_lossy(&out).into_owned())
    })
    .await;
    if !matches!(result, Ok(Ok(_))) {
        stop(&mut child).await;
    }
    result.context("Git inspection timed out")?
}
pub async fn call(
    root: &Path,
    name: &str,
    args: &Value,
    receipt: Option<&crate::team_workspace::Receipt>,
) -> Result<Value> {
    let root = root.canonicalize()?;
    // Check pointers and repository configuration BEFORE rev-parse, which itself
    // reads configuration. Only an adapter-pinned receipt allows shared metadata.
    let project = if let Some(r) = receipt {
        r.validate_scope(&r.project_root, &root)?;
        r.verify()?;
        if r.git_dir.is_some() {
            &r.project_root
        } else {
            &root
        }
    } else {
        &root
    };
    crate::team_workspace::repository(&root, project).await?;
    for query in ["--show-toplevel", "--absolute-git-dir"] {
        let found = run(&root, &["rev-parse".into(), query.into()]).await?;
        let found = Path::new(found.trim()).canonicalize()?;
        ensure!(
            found.starts_with(&root)
                || (query == "--absolute-git-dir"
                    && receipt.is_some_and(|r| r.worktree_git_dir.as_ref() == Some(&found))),
            "Git repository/metadata differs from the registered workspace"
        );
    }
    // Status/diff can invoke clean/process filters, even with --no-ext-diff.
    // Do not follow included configuration or accept repository-defined helpers.
    let config = run(
        &root,
        &[
            "config".into(),
            "--local".into(),
            "--no-includes".into(),
            "--null".into(),
            "--list".into(),
        ],
    )
    .await?;
    for entry in config.split('\0') {
        let key = entry.split('\n').next().unwrap_or("").to_ascii_lowercase();
        ensure!(
            !key.starts_with("filter.")
                && !key.starts_with("include.")
                && !key.starts_with("includeif.")
                && key != "extensions.worktreeconfig",
            "Repository content filters, included config and worktree config require an explicitly authorized shell; inspection tools do not execute them"
        );
    }
    let mut command: Vec<String> = match name {
        "git_status" => vec![
            "status",
            "--porcelain=v1",
            "--branch",
            "--untracked-files=normal",
            "--ignore-submodules=all",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect(),
        "git_diff" => {
            let mut a = vec![
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--ignore-submodules=all",
            ]
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>();
            if args["staged"] == true {
                a.push("--cached".into());
            }
            a
        }
        "git_log" => vec![
            "log".into(),
            "--no-show-signature".into(),
            "--format=%H %ad %s".into(),
            "--date=iso-strict".into(),
            format!("-{}", args["limit"].as_u64().unwrap_or(20)),
        ],
        _ => anyhow::bail!("Unknown Git tool"),
    };
    command.push("--".into());
    if let Some(p) = args["path"].as_str() {
        crate::runner::path(&root, p)?;
        command.push(format!(":(literal){p}"));
    }
    Ok(json!({"text":run(&root,&command).await?,"read_only":true}))
}
