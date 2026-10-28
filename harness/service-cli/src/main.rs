//! Thin service client: no agent loop, provider credentials or local tool runner.
mod deployment;
mod settings;
use anyhow::{ensure, Context, Result};
use clap::{Parser, Subcommand};
use futures_util::StreamExt;
use openworker_sdk::service::{Service, Session, SessionOptions, SessionRef};
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};
#[derive(Parser)]
#[command(
    name = "ows",
    version,
    about = "Manage and submit agents to OpenWorker Service"
)]
struct Args {
    #[arg(long, global = true)]
    config: Option<PathBuf>,
    #[arg(long, global = true)]
    context: Option<String>,
    #[arg(long, global = true)]
    url: Option<String>,
    #[arg(long, global = true)]
    namespace: Option<String>,
    #[arg(long, global = true, conflicts_with = "token_env")]
    token_file: Option<PathBuf>,
    #[arg(long, global = true, conflicts_with = "token_file")]
    token_env: Option<String>,
    #[arg(long, global = true)]
    ca_file: Option<PathBuf>,
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    /// Plan and inspect cloud deployments independently of service authentication.
    Deploy {
        #[command(subcommand)]
        command: deployment::Command,
    },
    Context {
        #[command(subcommand)]
        command: ContextCommand,
    },
    /// Validate credentials, then save only their file reference and service identity.
    Login,
    Whoami,
    Pool {
        #[command(subcommand)]
        command: PoolCommand,
    },
    Configuration {
        #[command(subcommand)]
        command: ConfigurationCommand,
    },
    Credentials {
        #[command(subcommand)]
        command: CredentialCommand,
    },
    Audit {
        #[arg(long, default_value_t = 0)]
        after: i64,
    },
    Capabilities,
    Profile {
        #[command(subcommand)]
        command: CatalogCommand,
    },
    Agent {
        #[command(subcommand)]
        command: CatalogCommand,
    },
    /// Atomically admit a new session and its initial task; no client-side two-step.
    Submit {
        #[arg(long)]
        agent: String,
        #[arg(long, conflicts_with = "environment_id")]
        runtime_profile: Option<String>,
        #[arg(long, conflicts_with = "runtime_profile")]
        environment_id: Option<String>,
        #[arg(long, default_value = "default")]
        project: String,
        #[arg(
            long,
            conflicts_with = "message_file",
            required_unless_present = "message_file"
        )]
        message: Option<String>,
        #[arg(long, conflicts_with = "message", required_unless_present = "message")]
        message_file: Option<PathBuf>,
        #[arg(long)]
        idempotency_key: Option<String>,
        #[arg(long)]
        wait: bool,
    },
    Operation {
        #[command(subcommand)]
        command: OperationCommand,
    },
    Environment {
        #[command(subcommand)]
        command: EnvironmentCommand,
    },
    Status {
        #[arg(long)]
        session: String,
    },
    Close {
        #[arg(long)]
        session: String,
        #[arg(long)]
        idempotency_key: Option<String>,
    },
    Send {
        #[arg(long)]
        session: String,
        #[arg(long)]
        message_file: PathBuf,
        #[arg(long)]
        idempotency_key: Option<String>,
    },
    /// Stream durable events as NDJSON. Ctrl-C disconnects; it never cancels the run.
    Watch {
        #[arg(long)]
        session: String,
        #[arg(long)]
        run: String,
        #[arg(long, default_value_t = 0)]
        after: i64,
    },
    Approve {
        #[arg(long)]
        session: String,
        #[arg(long)]
        interaction: String,
        #[arg(long,value_parser=["once","session","deny"])]
        allow: String,
        #[arg(long)]
        idempotency_key: Option<String>,
    },
    Answer {
        #[arg(long)]
        session: String,
        #[arg(long)]
        interaction: String,
        #[arg(long)]
        answer_file: PathBuf,
        #[arg(long)]
        idempotency_key: Option<String>,
    },
    Cancel {
        #[arg(long)]
        session: String,
        #[arg(long)]
        run: String,
        #[arg(long)]
        idempotency_key: Option<String>,
    },
    Control {
        #[arg(long)]
        session: String,
        #[arg(long)]
        receipt: String,
    },
}
#[derive(Subcommand)]
enum PoolCommand {
    List,
    Scale {
        id: String,
        #[arg(long)]
        replicas: i32,
        #[arg(long)]
        expected_revision: i64,
        #[arg(long)]
        idempotency_key: String,
    },
}
#[derive(Subcommand)]
enum ConfigurationCommand {
    Get,
    Set {
        #[arg(long)]
        file: PathBuf,
        #[arg(long)]
        expected_revision: i64,
        #[arg(long)]
        idempotency_key: String,
    },
}
#[derive(Subcommand)]
enum CredentialCommand {
    List,
    /// Generate a private token file before sending; retries reuse the same file and spec.
    Issue {
        #[arg(long)]
        spec: PathBuf,
        #[arg(long = "out")]
        output_token_file: PathBuf,
        #[arg(long)]
        idempotency_key: String,
    },
    Revoke {
        id: String,
    },
}
#[derive(Subcommand)]
enum ContextCommand {
    Add { name: String },
    Use { name: String },
    List,
}
#[derive(Subcommand)]
enum CatalogCommand {
    Deploy {
        #[arg(long)]
        file: PathBuf,
        #[arg(long)]
        idempotency_key: Option<String>,
    },
    Activate {
        id: String,
        #[arg(long)]
        alias: Option<String>,
        #[arg(long)]
        idempotency_key: Option<String>,
    },
    Get {
        id: String,
    },
}
#[derive(Subcommand)]
enum OperationCommand {
    Get {
        id: String,
    },
    Wait {
        id: String,
        #[arg(long, default_value_t = 300)]
        timeout: u64,
    },
}
#[derive(Subcommand)]
enum EnvironmentCommand {
    Get {
        id: String,
    },
    List,
    Delete {
        id: String,
        #[arg(long)]
        idempotency_key: Option<String>,
    },
}
fn output(value: impl serde::Serialize) -> Result<()> {
    use std::io::Write;
    let mut out = std::io::stdout().lock();
    serde_json::to_writer(&mut out, &value)?;
    writeln!(out)?;
    out.flush()?;
    Ok(())
}
fn key(value: &Option<String>) -> String {
    value
        .clone()
        .unwrap_or_else(|| openworker_sdk::service::new_idempotency_key())
}
fn read(path: &PathBuf, limit: usize) -> Result<String> {
    use std::io::Read;
    let mut data = Vec::new();
    std::fs::File::open(path)
        .context("Cannot read input file")?
        .take(limit as u64 + 1)
        .read_to_end(&mut data)?;
    ensure!(data.len() <= limit, "Input file is too large");
    String::from_utf8(data).context("Input file must be UTF-8")
}
fn document(path: &PathBuf) -> Result<Value> {
    let text = read(path, 128000)?;
    serde_json::from_str(&text)
        .or_else(|_| serde_yaml::from_str(&text))
        .map_err(|_| anyhow::anyhow!("Invalid JSON/YAML document"))
}
fn component(s: &str) -> Result<&str> {
    ensure!(
        !s.is_empty()
            && s.len() <= 256
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-@.".contains(&b))
            && s != "."
            && s != "..",
        "Invalid resource ID"
    );
    Ok(s)
}
async fn session(service: &Service, sid: &str) -> Result<Session> {
    let s = service
        .json(
            "GET",
            &format!("/v1/sessions/{}", component(sid)?),
            None,
            None,
        )
        .await?;
    service
        .resume(SessionRef {
            service_id: service.capabilities()["serviceId"]
                .as_str()
                .context("Service identity absent")?
                .into(),
            namespace: service.capabilities()["namespace"].as_str().unwrap().into(),
            session_id: sid.into(),
            agent: s["service"]["releaseId"]
                .as_str()
                .or_else(|| service.capabilities()["agent"].as_str())
                .context("Agent identity absent")?
                .into(),
            environment_id: s["session"]["environment"]["environment_id"]
                .as_str()
                .context("Environment absent")?
                .into(),
        })
        .await
        .map_err(Into::into)
}
async fn response(
    service: &Service,
    sid: &str,
    iid: &str,
    response: &str,
    approval: bool,
    k: &str,
) -> Result<Value> {
    let s = session(service, sid).await?;
    let state = s.snapshot().await?;
    let i = state["interactions"]
        .as_array()
        .context("Interactions missing")?
        .iter()
        .find(|i| i["id"] == iid)
        .context("Pending interaction not found")?;
    ensure!(
        (i["kind"] == "approval") == approval,
        "Wrong interaction kind; use approve or answer accordingly"
    );
    let action = json!({"kind":"response","interaction_id":iid,"response":response,"binding":{"bundle_digest":i["bundle_digest"],"schema_digest":i["schema_digest"],"policy_digest":i["policy_digest"],"resource_policy_digest":i["resource_policy_digest"]}});
    Ok(s.control(
        i["turn_id"].as_str().context("Run ID missing")?,
        action,
        Some(k),
    )
    .await?)
}
#[tokio::main]
async fn main() {
    if let Err(e) = run(Args::parse()).await {
        if let Some(e) = e.downcast_ref::<openworker_sdk::service::Error>() {
            eprintln!(
                "{}",
                json!({"error":e.code,"status":e.status,"idempotencyKey":e.idempotency_key,"operationId":e.operation_id})
            );
        } else {
            eprintln!("{e}");
        }
        std::process::exit(1)
    }
}
async fn run(args: Args) -> Result<()> {
    if let Command::Deploy { command } = &args.command {
        return deployment::run(command);
    }
    let mut settings = settings::Settings::load(args.config.as_ref())?;
    if let Command::Context { command } = &args.command {
        return match command {
            ContextCommand::Add { name } => {
                settings.add(name, &args)?;
                settings.save()?;
                output(json!({"context":name}))
            }
            ContextCommand::Use { name } => {
                ensure!(settings.data.contexts.contains_key(name), "Unknown context");
                settings.data.selected = Some(name.clone());
                settings.save()?;
                output(json!({"context":name}))
            }
            ContextCommand::List => output(&settings.data),
        };
    };
    let service = settings.connect(&args).await?;
    if matches!(args.command, Command::Login) {
        settings.login(&args, &service)?;
        settings.save()?;
        return output(service.identity());
    }
    match args.command {
        Command::Whoami => output(service.identity())?,
        Command::Capabilities => output(service.capabilities())?,
        Command::Profile { command } => catalog(&service, "profile", command).await?,
        Command::Agent { command } => catalog(&service, "agent", command).await?,
        Command::Pool{command}=>match command {
            PoolCommand::List=>output(service.json("GET","/v1/worker-pools",None,None).await?)?,
            PoolCommand::Scale{id,replicas,expected_revision,idempotency_key}=>output(service.json("POST",&format!("/v1/worker-pools/{}/scale",component(&id)?),Some(json!({"replicas":replicas,"expectedRevision":expected_revision})),Some(&idempotency_key)).await?)?,
        },
        Command::Configuration{command}=>match command {
            ConfigurationCommand::Get=>output(service.json("GET","/v1/configuration",None,None).await?)?,
            ConfigurationCommand::Set{file,expected_revision,idempotency_key}=>output(service.json("PUT","/v1/configuration",Some(json!({"expectedRevision":expected_revision,"configuration":document(&file)?})),Some(&idempotency_key)).await?)?,
        },
        Command::Credentials { command } => match command {
            CredentialCommand::List => {
                output(service.json("GET", "/v1/credentials", None, None).await?)?
            }
            CredentialCommand::Revoke { id } => output(
                service
                    .json(
                        "POST",
                        &format!("/v1/credentials/{}/revoke", component(&id)?),
                        Some(json!({})),
                        None,
                    )
                    .await?,
            )?,
            CredentialCommand::Issue {
                spec,
                output_token_file: token_file,
                idempotency_key,
            } => {
                let spec = document(&spec)?;
                ensure!(
                    spec["expires_at"].as_i64().is_some(),
                    "Credential spec requires expires_at (Unix seconds)"
                );
                if !token_file.exists() {
                    use std::io::Write;
                    let mut opts = std::fs::OpenOptions::new();
                    opts.write(true).create_new(true);
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::OpenOptionsExt;
                        opts.mode(0o600);
                    }
                    let mut file = opts
                        .open(&token_file)
                        .context("Cannot create private token file")?;
                    let token = zeroize::Zeroizing::new(format!("{}{}", key(&None), key(&None)));
                    file.write_all(token.as_bytes())?;
                    file.sync_all()?;
                }
                let token = settings::token(&token_file)?;
                output(
                    service
                        .json(
                            "POST",
                            "/v1/credentials",
                            Some(json!({"spec":spec,"token":token.as_str()})),
                            Some(&idempotency_key),
                        )
                        .await?,
                )?;
            }
        },
        Command::Audit { after } => {
            ensure!(after >= 0, "Invalid audit cursor");
            output(
                service
                    .json("GET", &format!("/v1/audit?after={after}"), None, None)
                    .await?,
            )?;
        }
        Command::Submit {
            agent,
            runtime_profile,
            environment_id,
            project,
            message,
            message_file,
            idempotency_key,
            wait,
        } => {
            let text = match message_file {
                Some(path) => read(&path, 32000)?,
                None => message.unwrap(),
            };
            let k = key(&idempotency_key);
            let mut op = service
                .submit(
                    &agent,
                    SessionOptions {
                        runtime_profile,
                        environment_id,
                        project: Some(project),
                        ..Default::default()
                    },
                    &text,
                    &k,
                )
                .await?;
            if wait {
                op = service
                    .wait_operation(&op.id, Duration::from_secs(300))
                    .await
                    .map_err(|mut e| {
                        e.idempotency_key = Some(k.clone());
                        e
                    })?;
            }
            output(json!({"idempotencyKey":k,"operation":op}))?;
        }
        Command::Operation { command } => match command {
            OperationCommand::Get { id } => output(service.operation(&id).await?)?,
            OperationCommand::Wait { id, timeout } => {
                ensure!(
                    (1..=3600).contains(&timeout),
                    "Timeout must be 1–3600 seconds"
                );
                output(
                    service
                        .wait_operation(&id, Duration::from_secs(timeout))
                        .await?,
                )?;
            }
        },
        Command::Environment {
            command: EnvironmentCommand::Get { id },
        } => output(service.environment(&id).await?)?,
        Command::Environment {
            command: EnvironmentCommand::List,
        } => output(service.json("GET", "/v1/environments", None, None).await?)?,
        Command::Environment {
            command:
                EnvironmentCommand::Delete {
                    id,
                    idempotency_key,
                },
        } => {
            let k = key(&idempotency_key);
            let mut receipt = service
                .json(
                    "POST",
                    &format!("/v1/environments/{}/delete", component(&id)?),
                    Some(json!({})),
                    Some(&k),
                )
                .await?;
            receipt["idempotencyKey"] = json!(k);
            output(receipt)?;
        }
        Command::Close {
            session: sid,
            idempotency_key,
        } => {
            let k = key(&idempotency_key);
            output(
                service
                    .json(
                        "POST",
                        &format!("/v1/sessions/{}/close", component(&sid)?),
                        Some(json!({})),
                        Some(&k),
                    )
                    .await?,
            )?;
        }
        Command::Status { session: sid } => {
            output(session(&service, &sid).await?.snapshot().await?)?
        }
        Command::Send {
            session: sid,
            message_file,
            idempotency_key,
        } => {
            let k = key(&idempotency_key);
            let run = session(&service, &sid)
                .await?
                .send(&read(&message_file, 32000)?, Some(&k))
                .await?;
            output(
                json!({"sessionId":sid,"runId":run.id,"submissionId":run.submission_id,"idempotencyKey":k}),
            )?;
        }
        Command::Watch {
            session: sid,
            run,
            after,
        } => {
            let record = service
                .json(
                    "GET",
                    &format!(
                        "/v1/sessions/{}/runs/{}",
                        component(&sid)?,
                        component(&run)?
                    ),
                    None,
                    None,
                )
                .await?;
            let run = session(&service, &sid)
                .await?
                .resume_run(
                    record["submissionId"]
                        .as_str()
                        .context("Submission absent")?,
                )
                .await?;
            let mut stream = run.events(after);
            loop {
                tokio::select! {_=tokio::signal::ctrl_c()=>{service.disconnect();return Ok(())},next=stream.next()=>match next{Some(e)=>output(e?)?,None=>break}}
            }
            output(json!({"result":run.snapshot().await?}))?;
        }
        Command::Approve {
            session: sid,
            interaction,
            allow,
            idempotency_key,
        } => {
            let k = key(&idempotency_key);
            output(
                json!({"idempotencyKey":k,"receipt":response(&service,&sid,&interaction,&allow,true,&k).await?}),
            )?;
        }
        Command::Answer {
            session: sid,
            interaction,
            answer_file,
            idempotency_key,
        } => {
            let k = key(&idempotency_key);
            output(
                json!({"idempotencyKey":k,"receipt":response(&service,&sid,&interaction,read(&answer_file,4000)?.trim(),false,&k).await?}),
            )?;
        }
        Command::Cancel {
            session: sid,
            run,
            idempotency_key,
        } => {
            let k = key(&idempotency_key);
            output(
                json!({"idempotencyKey":k,"receipt":session(&service,&sid).await?.control(&run,json!({"kind":"cancel"}),Some(&k)).await?}),
            )?;
        }
        Command::Control {
            session: sid,
            receipt,
        } => output(
            session(&service, &sid)
                .await?
                .control_receipt(&receipt)
                .await?,
        )?,
        Command::Context { .. } | Command::Login | Command::Deploy { .. } => unreachable!(),
    }
    service.disconnect();
    Ok(())
}
async fn catalog(service: &Service, kind: &str, command: CatalogCommand) -> Result<()> {
    match command {
        CatalogCommand::Deploy {
            file,
            idempotency_key,
        } => {
            let k = key(&idempotency_key);
            let value = document(&file)?;
            let entry = if kind == "profile" {
                service
                    .register_runtime_profile(
                        &serde_json::from_value(value).context("Invalid RuntimeProfile")?,
                        &k,
                    )
                    .await?
            } else {
                service
                    .deploy_agent(
                        &serde_json::from_value(value).context("Invalid agent release")?,
                        &k,
                    )
                    .await?
            };
            output(json!({"idempotencyKey":k,"entry":entry}))?;
        }
        CatalogCommand::Activate {
            id,
            alias,
            idempotency_key,
        } => {
            let k = key(&idempotency_key);
            let entry = if kind == "profile" {
                service
                    .activate_runtime_profile(&id, alias.as_deref(), &k)
                    .await?
            } else {
                service.activate_agent(&id, alias.as_deref(), &k).await?
            };
            output(json!({"idempotencyKey":k,"entry":entry}))?;
        }
        CatalogCommand::Get { id } => {
            let path = if kind == "profile" {
                "profiles"
            } else {
                "releases"
            };
            output(
                service
                    .json(
                        "GET",
                        &format!("/v1/{path}/{}", component(&id)?),
                        None,
                        None,
                    )
                    .await?,
            )?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod command_tests {
    use super::*;
    #[test]
    fn credential_output_does_not_override_the_authentication_file() {
        let args = Args::try_parse_from([
            "ows",
            "--token-file",
            "admin.token",
            "credentials",
            "issue",
            "--spec",
            "runner.json",
            "--out",
            "new.token",
            "--idempotency-key",
            "issue-1",
        ])
        .unwrap();
        assert_eq!(args.token_file, Some(PathBuf::from("admin.token")));
        match args.command {
            Command::Credentials {
                command:
                    CredentialCommand::Issue {
                        output_token_file: token_file,
                        ..
                    },
            } => assert_eq!(token_file, PathBuf::from("new.token")),
            _ => panic!("wrong command"),
        }
    }
}
