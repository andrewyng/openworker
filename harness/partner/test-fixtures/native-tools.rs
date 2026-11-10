use openworker_tools::{Tool, ToolContext, ToolDescriptor, ToolFuture, ToolOutput, ToolRegistry};
use serde_json::{json, Value};

struct Note;
impl Tool for Note {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: "partner.note".into(),
            version: "1".into(),
            description: "Write one local test marker".into(),
            input_schema: json!({"type":"object","properties":{},"additionalProperties":false}),
        }
    }
    fn call(&self, context: ToolContext, _: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            use std::io::Write;
            let mut file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(context.workspace.join("partner-proof.txt"))
                .map_err(|_| {
                    openworker_tools::ToolError::new("write_failed", "Cannot write test marker")
                })?;
            file.write_all(b"native partner tool\n").map_err(|_| {
                openworker_tools::ToolError::new("write_failed", "Cannot write test marker")
            })?;
            Ok(ToolOutput::json(json!({"written":true})))
        })
    }
}
pub fn register(registry: &mut ToolRegistry) -> anyhow::Result<()> {
    registry.register(Note)
}
