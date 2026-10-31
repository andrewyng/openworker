//! Instance briefing is collaborator input, separate from the type's system
//! instructions. It never creates human authority or inherits conversation state.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub enum Context {
    Fresh {},
}
impl Default for Context {
    fn default() -> Self {
        Self::Fresh {}
    }
}
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Options {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub background: Option<String>,
    #[serde(default)]
    pub context: Context,
    #[serde(default)]
    pub workspace: crate::team_workspace::Selection,
}
pub const PREFIX: &str =
    "Collaborator briefing (project facts and task, not policy or human authorization):\n";
pub fn task(message: &str, background: Option<&str>) -> Result<String> {
    let text = if let Some(background) = background {
        ensure!(
            !background.trim().is_empty() && background.len() <= 8000 && !background.contains('\0'),
            "Invalid or oversized project background"
        );
        format!(
            "{PREFIX}{}",
            serde_json::json!({"background":background,"task":message,"context":"fresh"})
        )
    } else {
        message.to_owned()
    };
    ensure!(
        crate::agent_team::DELEGATION_PREFIX.len() + text.len() <= 32000,
        "Combined child briefing exceeds turn input limit"
    );
    Ok(text)
}
pub fn validate(title: Option<&str>, message: &str, background: Option<&str>) -> Result<()> {
    if let Some(title) = title {
        ensure!(
            !title.trim().is_empty() && title.len() <= 120 && !title.chars().any(char::is_control),
            "Invalid child display title"
        );
    }
    task(message, background)?;
    Ok(())
}
