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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content: Option<Vec<crate::attachments::Part>>,
    pub instruction_addendum: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub auto_approver_context: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub compaction: Option<crate::compaction::Instructions>,
    pub output: OutputOptions,
}
impl Context {
    /// Idempotency evidence; avoids repeating trusted instructions in events.
    pub fn digest(&self) -> Result<String> {
        Ok(crate::bundle::digest(&serde_json::to_vec(self)?))
    }
    pub fn validate(&self) -> Result<()> {
        validate_guidance("autoApproverContext", self.auto_approver_context.as_deref())?;
        if let Some(options) = &self.compaction {
            options.validate()?;
        }
        if let Some(parts) = &self.content {
            crate::attachments::validate_parts(parts)?;
        }
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
            content: p
                .get("content")
                .filter(|v| !v.is_null())
                .map(|v| serde_json::from_value(v.clone()))
                .transpose()?,
            instruction_addendum: p
                .get("instruction_addendum")
                .filter(|v| !v.is_null())
                .map(|v| serde_json::from_value(v.clone()))
                .transpose()?,
            auto_approver_context: p
                .get("auto_approver_context")
                .filter(|v| !v.is_null())
                .map(|v| serde_json::from_value(v.clone()))
                .transpose()?,
            compaction: p
                .get("compaction")
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

/// Validate without echoing application context, which may contain private facts.
pub fn validate_guidance(name: &str, text: Option<&str>) -> Result<()> {
    if let Some(text) = text {
        ensure!(text.len() <= 32_000, "{name} exceeds 32,000 bytes");
        ensure!(!text.contains('\0'), "{name} contains a null character");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn application_guidance_validation_and_legacy_digest() {
        // Omitting the new options must preserve old submission receipts.
        let old =
            br#"{"instruction_addendum":null,"output":{"reasoning":false,"bufferAttempts":false}}"#;
        assert_eq!(
            Context::default().digest().unwrap(),
            crate::bundle::digest(old)
        );
        for request in [
            serde_json::json!({"auto_approver_context":false}),
            serde_json::json!({"auto_approver_context":"secret\u{0}"}),
            serde_json::json!({"auto_approver_context":"x".repeat(32001)}),
            serde_json::json!({"compaction":{"additional_instructions":"x".repeat(32001)}}),
            serde_json::json!({"compaction":{"enabled":false}}),
        ] {
            assert!(Context::from_request(&request).is_err());
        }
        for text in ["", "Preserve these decisions"] {
            assert!(Context::from_request(&serde_json::json!({"auto_approver_context":text,"compaction":{"additional_instructions":text}})).is_ok());
        }
    }
}
