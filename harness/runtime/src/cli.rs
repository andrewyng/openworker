use crate::{execution::Backend, model::Model, runtime::Runtime, store::Store};
use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use serde_json::json;
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    name = "openworker",
    version,
    about = "Native OpenWorker runtime — explicit host execution on Windows; OpenShell on Linux"
)]
struct Args {
    #[command(subcommand)]
    command: Command,
}
#[derive(clap::Args)]
struct RuntimeArgs {
    #[arg(long)]
    state_dir: PathBuf,
    #[arg(long)]
    bundle: PathBuf,
    /// Trusted app-owned MCP providers; independent of coworker tool selection.
    #[arg(long)]
    servers: Option<PathBuf>,
    /// Trusted host-owned classification file, outside bundle and workspace.
    #[arg(long)]
    tool_policy: Option<PathBuf>,
    /// Trusted app-owned placement/files/network/credential policy.
    #[arg(long)]
    runtime_policy: Option<PathBuf>,
    /// Native Linux tool runner/supervisor executable (defaults to this binary).
    #[arg(long)]
    runner: Option<PathBuf>,
    #[arg(long, default_value = "gpt-6-sol")]
    model: String,
    #[arg(long, value_enum, default_value = "openai")]
    provider: crate::provider::Kind,
    /// Explicit scripted provider for integration tests; still uses real tool processes.
    #[arg(long)]
    scripted: bool,
    /// Explicit off runs tools with host-account access, without kernel isolation.
    #[arg(long, value_enum, default_value = "openshell")]
    sandbox: Backend,
}
impl RuntimeArgs {
    fn open(&self, registry: &openworker_tools::ToolRegistry) -> Result<std::sync::Arc<Runtime>> {
        let mut registry = registry.clone();
        if let Some(path) = &self.servers {
            crate::servers::load(path, &self.bundle, &mut registry)?;
        }
        if self.sandbox == Backend::Off {
            eprintln!("WARNING: sandbox OFF. Tool processes have host-account file and network access. Approval mode is separate.");
        }
        Runtime::open_with_registry(
            &self.state_dir,
            &self.bundle,
            &self.runner.clone().unwrap_or(std::env::current_exe()?),
            Model::new(self.model.clone(), self.scripted).with_provider(self.provider),
            self.sandbox,
            self.tool_policy.as_deref(),
            self.runtime_policy.as_deref(),
            registry.clone(),
        )
    }
}
#[derive(Subcommand)]
enum Command {
    /// Create/secure app-owned state under the current account (not a sandbox).
    PrepareDirectory {
        #[arg(long)]
        path: PathBuf,
    },
    /// Inspect validated bundle identity and exact tool routes without execution.
    InspectBundle {
        #[arg(long)]
        bundle: PathBuf,
        #[arg(long)]
        servers: Option<PathBuf>,
    },
    Doctor {
        #[arg(long)]
        json: bool,
        #[arg(long, value_enum, default_value = "openshell")]
        sandbox: Backend,
    },
    Sandbox {
        #[command(subcommand)]
        command: SandboxCommand,
    },
    Serve {
        #[arg(long, required = true)]
        stdio: bool,
        #[command(flatten)]
        runtime: RuntimeArgs,
    },
    Run {
        #[command(flatten)]
        runtime: RuntimeArgs,
        #[arg(long)]
        workspace: PathBuf,
        #[arg(long)]
        coworker: String,
        #[arg(long)]
        task: String,
        #[arg(long)]
        compaction_cap: Option<usize>,
        #[arg(long)]
        context_window: Option<usize>,
        /// Auto-trim if summarization fails; does NOT bypass tool approvals.
        #[arg(long)]
        compaction_auto_trim: bool,
        #[arg(long, value_enum, default_value = "ask")]
        approval_mode: crate::permissions::Mode,
        #[arg(long)]
        auto_skip_questions: bool,
        /// JSON grant selection (profile/files/network/credentials); values are not secrets.
        #[arg(long)]
        policy_selection: Option<PathBuf>,
    },
    Sessions {
        #[arg(long)]
        state_dir: PathBuf,
        #[arg(long)]
        inspect: Option<String>,
    },
    /// Internal/example MCP process. Launched by the selected execution backend.
    #[command(hide = true)]
    McpExample {
        #[arg(long)]
        workspace: PathBuf,
        #[arg(long, default_value = "example")]
        label: String,
    },
    /// First-party coding/process tools in the selected execution environment.
    #[command(hide = true)]
    McpCoding {
        #[arg(long)]
        workspace: PathBuf,
        #[arg(long, hide = true)]
        environment_processes: bool,
        #[arg(long, hide = true)]
        workspace_receipt: Option<String>,
        /// Explicit opt-in; requires OpenShell-issued placeholders, never raw keys.
        #[arg(long, hide = true)]
        openshell_git: bool,
        #[arg(long, hide = true, requires = "openshell_git")]
        openshell_git_host: Option<String>,
        #[arg(long, hide = true, requires = "openshell_git")]
        openshell_git_repository: Vec<String>,
    },
    /// Private Git credential helper. Never persists credentials.
    #[command(hide = true)]
    GitCredentialOpenshell { action: String },
    /// Trusted workspace provisioning channel; never a model tool.
    #[command(hide = true)]
    WorkspaceStdio {
        #[arg(long)]
        root: PathBuf,
    },
    /// Internal sandbox-local process manager; never a model tool.
    #[cfg(target_os = "linux")]
    #[command(hide = true)]
    CodingProcessManager {
        #[arg(long)]
        workspace: PathBuf,
    },
    /// Internal direct-tool lifecycle supervisor; never exposed as a model tool.
    #[command(hide = true)]
    DirectSupervisor {
        #[arg(long)]
        owner: u32,
        #[arg(long)]
        token: String,
        #[arg(long)]
        workspace: PathBuf,
        #[arg(required = true, trailing_var_arg = true)]
        command: Vec<String>,
    },
}
#[derive(Subcommand)]
enum SandboxCommand {
    Setup {
        #[arg(long)]
        apply: bool,
    },
    Verify {
        #[arg(long)]
        state_dir: PathBuf,
        #[arg(long)]
        bundle: PathBuf,
        #[arg(long)]
        runner: Option<PathBuf>,
    },
}
pub fn main_with_registry(registry: openworker_tools::ToolRegistry) {
    let args = Args::parse();
    // Supervisor must own/reap its children without a Tokio background reaper.
    if let Command::DirectSupervisor {
        owner,
        workspace,
        command,
        token,
    } = &args.command
    {
        match crate::direct::supervise(*owner, workspace, command, token) {
            Ok(code) => std::process::exit(code),
            Err(_) => {
                eprintln!("Direct supervisor failed");
                std::process::exit(1);
            }
        }
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("async runtime");
    let result = runtime.block_on(entry(args, registry));
    runtime.shutdown_timeout(std::time::Duration::from_secs(2));
    if let Err(e) = result {
        eprintln!("openworker: {e:#}");
        std::process::exit(1);
    }
}
async fn entry(args: Args, registry: openworker_tools::ToolRegistry) -> Result<()> {
    match args.command {
        Command::PrepareDirectory { path } => {
            crate::platform::validate_path(&path)?;
            if !path.is_absolute() {
                bail!("Absolute app directory required");
            }
            crate::platform::private_directory(&path)?;
            println!("{}", json!({"prepared":true,"path":path.canonicalize()?}));
        }
        Command::Doctor { json: _, sandbox } => {
            let report = match sandbox {
                Backend::Openshell => crate::sandbox::doctor().await,
                Backend::Off => crate::direct::doctor(),
            };
            println!("{}", serde_json::to_string_pretty(&report)?);
            if report["ready"] != true {
                bail!("Selected execution backend unavailable; no automatic fallback");
            }
        }
        Command::Sandbox {
            command: SandboxCommand::Setup { apply },
        } => println!(
            "{}",
            serde_json::to_string_pretty(&crate::sandbox::setup(apply).await?)?
        ),
        Command::Sandbox {
            command:
                SandboxCommand::Verify {
                    state_dir,
                    bundle,
                    runner,
                },
        } => {
            let report = crate::sandbox::verify(
                &state_dir,
                &bundle,
                &runner.unwrap_or(std::env::current_exe()?),
            )
            .await?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::Serve { runtime, .. } => {
            let rt = runtime.open(&registry)?;
            crate::protocol::serve(rt).await?;
        }
        Command::Sessions { state_dir, inspect } => {
            let db = Store::open(&state_dir)?;
            if let Some(sid) = inspect {
                println!("{}", serde_json::to_string_pretty(&db.get(&sid)?)?);
            } else {
                println!("{}", serde_json::to_string_pretty(&db.list()?)?);
            }
        }
        Command::InspectBundle { bundle, servers } => {
            let mut registry = registry;
            if let Some(path) = servers {
                crate::servers::load(&path, &bundle, &mut registry)?;
            }
            let b = crate::bundle::Bundle::load_with_registry(&bundle, &registry)?;
            println!(
                "{}",
                serde_json::to_string_pretty(
                    &json!({"coworker":b.id,"bundle_digest":b.digest,"servers":b.tools.servers.keys().collect::<Vec<_>>(),"bindings":b.tools.bindings,"package":b.manifest,"skills":crate::skills::catalog(&b.skills,&b.skills.keys().cloned().collect::<Vec<_>>())})
                )?
            );
        }
        Command::McpCoding {
            workspace,
            environment_processes,
            workspace_receipt,
            openshell_git,
            openshell_git_host,
            openshell_git_repository,
        } => {
            crate::coding::serve_with_git(
                &workspace,
                environment_processes,
                workspace_receipt
                    .as_deref()
                    .map(serde_json::from_str)
                    .transpose()?,
                openshell_git.then(|| crate::coding::openshell_git::Settings {
                    host: openshell_git_host.unwrap_or_else(|| "github.com".into()),
                    repositories: openshell_git_repository,
                }),
            )
            .await?
        }
        Command::GitCredentialOpenshell { action } => {
            crate::coding::openshell_git::credential_helper(&action).await?
        }
        Command::WorkspaceStdio { root } => crate::team_workspace::serve(&root).await?,
        #[cfg(target_os = "linux")]
        Command::CodingProcessManager { workspace } => {
            crate::coding::manager::serve(&workspace).await?
        }
        Command::McpExample { workspace, label } => {
            crate::runner::serve_labeled(&workspace, &label).await?
        }
        Command::DirectSupervisor { .. } => unreachable!("supervisor handled before async runtime"),
        Command::Run {
            runtime,
            workspace,
            coworker,
            task,
            compaction_cap,
            context_window,
            compaction_auto_trim,
            approval_mode,
            auto_skip_questions,
            policy_selection,
        } => {
            use std::io::{IsTerminal, Write};
            if !std::io::stdin().is_terminal() {
                bail!("Interactive run requires a terminal for approvals. Integrators should use serve --stdio.");
            }
            let rt = runtime.open(&registry)?;
            let mut events = rt.events.subscribe();
            let mut compaction = crate::compaction::Config::default();
            if let Some(cap) = compaction_cap {
                compaction.cap_tokens = cap;
            }
            if let Some(window) = context_window {
                compaction.context_window = window;
            }
            compaction.auto_trim_on_failure = compaction_auto_trim;
            let mut params = json!({"name":"CLI task","coworker":coworker,"workspace":workspace,"compaction":compaction,"permissions":{"mode":approval_mode,"auto_skip_questions":auto_skip_questions}});
            if let Some(path) = policy_selection {
                let selection: serde_json::Value =
                    serde_json::from_slice(&crate::bundle::read_limited(&path, 64000)?)?;
                let preview = rt
                    .dispatch(
                        "policy/resolve",
                        json!({"coworker":coworker,"workspace":workspace,"policy":selection}),
                    )
                    .await?;
                eprintln!(
                    "Selected runtime policy: {}",
                    serde_json::to_string_pretty(&preview["policy"])?
                );
                params["policy"] = selection;
                params["expected_policy_digest"] = preview["policy"]["digest"].clone();
            }
            let s = rt.dispatch("session/create", params).await?;
            let sid = s["session"]["id"]
                .as_str()
                .context("Missing session")?
                .to_owned();
            eprintln!(
                "Session {sid} · {} · sandbox {}",
                if runtime.scripted {
                    "SCRIPTED TEST"
                } else {
                    "LIVE"
                },
                runtime.sandbox.name()
            );
            rt.dispatch(
                "turn/start",
                json!({"session_id":sid,"submission_id":crate::id("submit"),"text":task}),
            )
            .await?;
            let completed = loop {
                let e = tokio::select! {
                    e=events.recv()=>e?,
                    _=tokio::signal::ctrl_c()=>{cancel(&rt,&sid).await?;continue;}
                };
                if e.session_id != sid {
                    continue;
                }
                match e.kind.as_str() {
                    "compacting" | "compacted" | "model_retrying" | "continuation" => {
                        eprintln!("\n{}: {}", e.kind, e.data);
                    }
                    "assistant_delta" => {
                        print!("{}", e.data["text"].as_str().unwrap_or(""));
                        std::io::stdout().flush()?;
                    }
                    "interaction_required" => {
                        let i = e.data;
                        println!(
                            "\n{}\n{}",
                            i["kind"],
                            serde_json::to_string_pretty(&i["call"])?
                        );
                        let approval = i["kind"] == "approval";
                        loop {
                            print!(
                                "{}",
                                if approval {
                                    "Allow once [y], allow entire tool for this session [a], deny [N]? "
                                } else {
                                    "Answer (must match an offered option): "
                                }
                            );
                            std::io::stdout().flush()?;
                            let line = tokio::select! {
                                line=read_answer()=>line?,
                                _=tokio::signal::ctrl_c()=>{cancel(&rt,&sid).await?;break;}
                            };
                            let mut p = json!({"session_id":sid,"interaction_id":i["id"]});
                            if approval {
                                p["decision"] = json!(if line.trim().eq_ignore_ascii_case("y") {
                                    "once"
                                } else if line.trim().eq_ignore_ascii_case("a") {
                                    "session"
                                } else {
                                    "deny"
                                });
                            } else {
                                p["answer"] = json!(line.trim());
                            }
                            match rt.dispatch("interaction/respond", p).await {
                            Ok(_)=>break,
                            Err(err)=>eprintln!("Response not accepted: {err}. Try again or press Ctrl+C to cancel."),
                        }
                        }
                    }
                    "tool_finished" => println!("\nTool result: {}", e.data),
                    "turn_completed" => {
                        println!("\n{}", e.data);
                        break e.data["status"] == "completed";
                    }
                    _ => {}
                }
            };
            rt.shutdown().await?;
            if !completed {
                bail!("Run did not complete successfully; its execution record is saved");
            }
        }
    }
    Ok(())
}

#[cfg(windows)]
async fn read_answer() -> Result<String> {
    use tokio::io::AsyncBufReadExt;
    let mut line = String::new();
    tokio::io::BufReader::new(tokio::io::stdin())
        .read_line(&mut line)
        .await?;
    Ok(line)
}

async fn cancel(rt: &std::sync::Arc<Runtime>, sid: &str) -> Result<()> {
    let snap = rt.snapshot(sid)?;
    rt.dispatch(
        "turn/cancel",
        json!({"session_id":sid,"turn_id":snap["turn"]["id"]}),
    )
    .await?;
    eprintln!("\nCancellation requested; waiting for sandbox cleanup.");
    Ok(())
}

// Read a terminal without a detached blocking task, so Ctrl+C during a question
// cannot leave process shutdown waiting forever for an outstanding stdin read.
#[cfg(not(windows))]
async fn read_answer() -> Result<String> {
    use std::io::Read;
    use std::os::fd::{AsRawFd, FromRawFd};
    struct Input {
        file: std::fs::File,
        flags: i32,
    }
    impl Drop for Input {
        fn drop(&mut self) {
            unsafe {
                libc::fcntl(self.file.as_raw_fd(), libc::F_SETFL, self.flags);
            }
        }
    }
    let fd = unsafe { libc::dup(libc::STDIN_FILENO) };
    if fd < 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let file = unsafe { std::fs::File::from_raw_fd(fd) };
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    impl AsRawFd for Input {
        fn as_raw_fd(&self) -> i32 {
            self.file.as_raw_fd()
        }
    }
    let input = tokio::io::unix::AsyncFd::new(Input { file, flags })?;
    let mut bytes = vec![];
    loop {
        let mut ready = input.readable().await?;
        let mut b = [0u8];
        match ready.try_io(|io| (&io.get_ref().file).read(&mut b)) {
            Ok(Ok(0)) => bail!("Terminal input closed"),
            Ok(Ok(_)) => {
                if b[0] == b'\n' {
                    return Ok(String::from_utf8(bytes)?);
                }
                bytes.push(b[0]);
                if bytes.len() > 4000 {
                    bail!("Answer exceeds 4000 bytes");
                }
            }
            Ok(Err(e)) => return Err(e.into()),
            Err(_) => {}
        }
    }
}
