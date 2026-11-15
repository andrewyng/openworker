//! Partner-owned Windows tools. No dependency on OpenWorker's engine.
#[cfg(windows)]
use openworker_tools::ToolOutput;
use openworker_tools::{Tool, ToolContext, ToolDescriptor, ToolError, ToolFuture, ToolRegistry};
use serde_json::{json, Value};
#[cfg(windows)]
mod windows;
#[derive(Clone, Copy)]
enum Action {
    Computer,
    Audio,
    Volume,
    Settings,
}
struct DesktopTool(Action);
pub fn register(registry: &mut ToolRegistry) -> anyhow::Result<()> {
    for action in [
        Action::Computer,
        Action::Audio,
        Action::Volume,
        Action::Settings,
    ] {
        registry.register(DesktopTool(action))?;
    }
    Ok(())
}
impl Tool for DesktopTool {
    fn descriptor(&self) -> ToolDescriptor {
        let(name,description,schema)=match self.0 {
            Action::Computer=>("computer_status","Read Windows battery/power status and connected display names. Does not capture the screen.",json!({"type":"object","properties":{},"additionalProperties":false})),
            Action::Audio=>("audio_status","Read the default Windows audio output volume and mute status.",json!({"type":"object","properties":{},"additionalProperties":false})),
            Action::Volume=>("set_volume","Set the default Windows output volume to an exact percentage, without changing mute. Returns before/after readings. Does not play audio.",json!({"type":"object","properties":{"percent":{"type":"integer","minimum":0,"maximum":100}},"required":["percent"],"additionalProperties":false})),
            Action::Settings=>("open_settings","Open the chosen Windows settings page. Returns launch status only; does not change settings or capture anything.",json!({"type":"object","properties":{"page":{"type":"string","enum":["sound","display","wifi"]}},"required":["page"],"additionalProperties":false})),
        };
        ToolDescriptor {
            qualified_name: format!("device.{name}"),
            description: description.into(),
            input_schema: schema,
            version: "1".into(),
        }
    }
    fn call(&self, context: ToolContext, args: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            if context.cancellation.is_cancelled() {
                return Err(ToolError::cancelled());
            }
            validate(self.0, &args)?;
            #[cfg(windows)]
            {
                let action = self.0;
                context
                    .progress("Contacting Windows", json!({"kind":"device_action"}))
                    .await?;
                let token = context.cancellation.clone();
                let result = tokio::task::spawn_blocking(move || {
                    if token.is_cancelled() {
                        return Err(ToolError::cancelled());
                    }
                    windows::execute(action, args)
                })
                .await
                .map_err(|_| {
                    ToolError::new(
                        "device_error",
                        "Windows operation interrupted; effects may be unknown",
                    )
                })??;
                Ok(ToolOutput::json(result))
            }
            #[cfg(not(windows))]
            {
                Err(ToolError::new(
                    "unsupported_platform",
                    "This tool requires native Windows. No simulated result was substituted.",
                ))
            }
        })
    }
}
fn validate(action: Action, args: &Value) -> Result<(), ToolError> {
    let object = args
        .as_object()
        .ok_or_else(|| ToolError::new("invalid_arguments", "Expected object"))?;
    let valid = match action {
        Action::Computer | Action::Audio => object.is_empty(),
        Action::Volume => object.len() == 1 && args["percent"].as_u64().is_some_and(|p| p <= 100),
        Action::Settings => {
            object.len() == 1 && matches!(args["page"].as_str(), Some("sound" | "display" | "wifi"))
        }
    };
    if !valid {
        return Err(ToolError::new(
            "invalid_arguments",
            "Unsupported device setting",
        ));
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bounded_controls() {
        assert!(validate(Action::Volume, &json!({"percent":30})).is_ok());
        for bad in [
            json!({"percent":101}),
            json!({"percent":-1}),
            json!({"percent":1.5}),
            json!({"percent":20,"command":"anything"}),
        ] {
            assert!(validate(Action::Volume, &bad).is_err());
        }
        assert!(validate(Action::Settings, &json!({"page":"sound"})).is_ok());
        assert!(validate(Action::Settings, &json!({"page":"ms-settings:anything"})).is_err());
    }
    #[test]
    fn catalog_has_no_capture_or_shell() {
        let mut registry = ToolRegistry::default();
        register(&mut registry).unwrap();
        for action in [
            Action::Computer,
            Action::Audio,
            Action::Volume,
            Action::Settings,
        ] {
            assert!(DesktopTool(action)
                .descriptor()
                .qualified_name
                .starts_with("device."));
        }
    }
}
