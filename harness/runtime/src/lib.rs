pub mod anthropic;
pub mod assistant_tools;
pub mod budget;
pub mod bundle;
pub mod chat_completions;
pub mod cli;
pub mod coding;
pub mod coding_handoff;
pub mod compaction;
pub mod connections;
pub mod control;
pub mod direct;
pub mod environment;
mod environment_client;
#[cfg(not(windows))]
pub mod environment_openshell;
pub mod execution;
pub mod http_mcp;
#[cfg(not(windows))]
pub mod io;
#[cfg(windows)]
#[path = "io_windows.rs"]
pub mod io;
pub mod model;
pub mod model_api;
pub mod package;
pub mod permissions;
pub mod planning;
pub mod platform;
pub mod protocol;
pub mod provider;
pub mod provider_auth;
pub mod resource_policy;
pub mod runner;
pub mod runtime;
#[cfg(not(windows))]
pub mod sandbox;
#[cfg(windows)]
#[path = "sandbox_unavailable.rs"]
pub mod sandbox;
pub mod servers;
pub mod skills;
pub mod store;
pub mod team_briefing;
pub mod team_coordination;
pub mod team_definition;
pub mod team_lifecycle;
pub mod team_placement;
pub mod team_service;
pub mod team_type_catalog;
pub mod team_workspace;
pub mod team_workspace_defaults;
pub mod teams;
mod tool_images;
pub mod tool_policy;
pub use openworker_telemetry as telemetry;

pub const PROTOCOL_VERSION: &str = "0.1";
pub const MAX_FRAME: usize = 1024 * 1024;
pub fn id(prefix: &str) -> String {
    format!("{prefix}_{}", uuid::Uuid::new_v4().simple())
}

pub mod agent_team;
