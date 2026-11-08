//! Trusted application context and presentation controls pinned to one turn.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase", deny_unknown_fields)]
pub struct OutputOptions {
    pub reasoning: bool,
    pub buffer_attempts: bool,
}

/// These options are stored with admission, not inserted as user transcript text.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Context {
    pub instruction_addendum: Option<String>,
    pub output: OutputOptions,
}
impl Context {
    /// Idempotency evidence; avoids repeating trusted instructions in events.
    pub fn digest(&self) -> Result<String> {
        Ok(crate::bundle::digest(&serde_json::to_vec(self)?))
    }
    pub fn validate(&self) -> Result<()> {
        if let Some(text) = &self.instruction_addendum {
            ensure!(
                text.len() <= 32000,
                "instructionAddendum exceeds 32,000 bytes"
            );
            ensure!(
                !text.contains('\0'),
                "instructionAddendum contains a null character"
            );
        }
        Ok(())
    }
    pub fn from_request(p: &serde_json::Value) -> Result<Self> {
        let value = Self {
            instruction_addendum: p
                .get("instruction_addendum")
                .filter(|v| !v.is_null())
                .map(|v| serde_json::from_value(v.clone()))
                .transpose()?,
            output: p
                .get("output")
                .filter(|v| !v.is_null())
                .map(|v| serde_json::from_value(v.clone()))
                .transpose()?
                .unwrap_or_default(),
        };
        value.validate()?;
        Ok(value)
    }
}
