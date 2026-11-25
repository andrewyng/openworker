use super::{component, document, output, read};
use anyhow::{Context, Result};
use clap::Subcommand;
use openworker_sdk::service::Service;
use serde_json::{json, Value};
use std::path::PathBuf;

#[derive(Subcommand)]
pub enum Command {
    List,
    Inspect {
        name: String,
    },
    Register {
        file: PathBuf,
        #[arg(long)]
        key_file: PathBuf,
    },
    Rotate {
        name: String,
        #[arg(long)]
        key_file: PathBuf,
    },
    Disable {
        name: String,
    },
    SetProfile {
        name: String,
        #[arg(long)]
        provider: String,
        #[arg(long)]
        model: String,
    },
}
pub async fn run(service: &Service, command: Command, id: String) -> Result<()> {
    let token = |file: &PathBuf| -> Result<String> {
        Ok(read(file, 4097)?.trim_end_matches(['\r', '\n']).to_owned())
    };
    let result = match command {
        Command::List => {
            service
                .json("GET", "/v1/model-providers", None, None)
                .await?
        }
        Command::Inspect { name } => service
            .json("GET", "/v1/model-providers", None, None)
            .await?["providers"]
            .as_array()
            .and_then(|rows| rows.iter().find(|r| r["name"] == name))
            .cloned()
            .context("Unknown provider")?,
        Command::Register { file, key_file } => {
            service
                .json(
                    "POST",
                    "/v1/model-providers",
                    Some(json!({"provider":document(&file)?,"apiKey":token(&key_file)?})),
                    Some(&id),
                )
                .await?
        }
        Command::Rotate { name, key_file } => {
            service
                .json(
                    "POST",
                    &format!("/v1/model-providers/{}/rotate", component(&name)?),
                    Some(json!({"apiKey":token(&key_file)?})),
                    Some(&id),
                )
                .await?
        }
        Command::Disable { name } => {
            service
                .json(
                    "POST",
                    &format!("/v1/model-providers/{}/disable", component(&name)?),
                    Some(Value::Null),
                    Some(&id),
                )
                .await?
        }
        Command::SetProfile {
            name,
            provider,
            model,
        } => {
            service
                .json(
                    "PUT",
                    &format!("/v1/model-profiles/{}", component(&name)?),
                    Some(json!({"connection":provider,"model":model})),
                    Some(&id),
                )
                .await?
        }
    };
    output(result)
}
