//! A partner-owned tool crate: no dependency on the OpenWorker agent loop.
use openworker_tools::{
    Tool, ToolContext, ToolDescriptor, ToolError, ToolFuture, ToolOutput, ToolRegistry,
};
use serde_json::{json, Value};

pub struct NotifyUser;
impl Tool for NotifyUser {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: "notifications.notify_user".into(),
            description:
                "Emit a notification for the application UI. Does not send an external message."
                    .into(),
            input_schema: schema(),
            version: "1".into(),
        }
    }
    fn call(&self, context: ToolContext, arguments: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            let (title, message) = fields(&arguments)?;
            context
                .progress(
                    "App notification",
                    json!({"kind":"user_notification", "title":title,"message":message}),
                )
                .await?;
            Ok(ToolOutput::json(
                json!({"delivery":"app_event","queued":true,"title":title,"message":message}),
            ))
        })
    }
}
pub fn register(registry: &mut ToolRegistry) -> anyhow::Result<()> {
    registry.register(NotifyUser)
}
fn schema() -> Value {
    json!({"type":"object","properties":{"title":{"type":"string","minLength":1,"maxLength":120},"message":{"type":"string","minLength":1,"maxLength":1000}},"required":["title","message"],"additionalProperties":false})
}
fn fields(arguments: &Value) -> Result<(&str, &str), ToolError> {
    let title = arguments["title"].as_str().unwrap_or_default();
    let message = arguments["message"].as_str().unwrap_or_default();
    if title.trim().is_empty()
        || title.len() > 480
        || message.trim().is_empty()
        || message.len() > 4000
    {
        return Err(ToolError::new(
            "invalid_arguments",
            "Title and message must be nonempty bounded strings",
        ));
    }
    Ok((title, message))
}

/// Explicit native UI demo, not used by the Linux application runtime. Calling this
/// directly does not supply harness approvals; the embedding app must authorize it.
#[cfg(target_os = "macos")]
pub struct MacDialog;
#[cfg(target_os = "macos")]
impl Tool for MacDialog {
    fn descriptor(&self) -> ToolDescriptor {
        NotifyUser.descriptor()
    }
    fn call(&self, context: ToolContext, arguments: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            let (title, message) = fields(&arguments)?;
            // Fixed script. Untrusted strings are argv values, never AppleScript.
            let script = "on run argv\nset r to display dialog (item 2 of argv) with title (item 1 of argv) buttons {\"OK\"} default button \"OK\" giving up after 5\nreturn gave up of r\nend run";
            let mut command = tokio::process::Command::new("/usr/bin/osascript");
            command
                .args(["-e", script, "--", title, message])
                .kill_on_drop(true);
            let result = tokio::select! { biased;
                _ = context.cancellation.cancelled() => return Err(ToolError::cancelled()),
                result = tokio::time::timeout(std::time::Duration::from_secs(10), command.output()) => result,
            };
            let output = result
                .map_err(|_| {
                    ToolError::new(
                        "timeout",
                        "Dialog status unknown after timeout; do not automatically retry",
                    )
                })?
                .map_err(|_| ToolError::new("launch_failed", "Could not launch macOS dialog"))?;
            if !output.status.success() {
                return Err(ToolError::new(
                    "dialog_failed",
                    "macOS dialog did not complete successfully",
                ));
            }
            let timed_out = match String::from_utf8_lossy(&output.stdout).trim() {
                "true" => true,
                "false" => false,
                _ => {
                    return Err(ToolError::new(
                        "unknown_status",
                        "Dialog returned an unrecognized status; do not automatically retry",
                    ))
                }
            };
            Ok(ToolOutput::json(
                json!({"delivery":"macos_dialog","acknowledged":!timed_out,"auto_dismissed":timed_out}),
            ))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn notification_has_stable_identity_and_structured_progress() {
        let (tx, mut rx) = tokio::sync::mpsc::channel(2);
        let context = ToolContext::new(
            "s".into(),
            "t".into(),
            "c".into(),
            std::env::temp_dir(),
            Default::default(),
            tx,
        );
        let output = NotifyUser
            .call(
                context,
                json!({"title":"Ready","message":"Review complete"}),
            )
            .await
            .unwrap();
        assert_eq!(output.data["delivery"], "app_event");
        assert_eq!(rx.recv().await.unwrap().data["kind"], "user_notification");
    }
    #[tokio::test]
    async fn cancellation_prevents_notification() {
        let (tx, mut rx) = tokio::sync::mpsc::channel(2);
        let token = openworker_tools::CancellationToken::new();
        token.cancel();
        let context = ToolContext::new(
            "s".into(),
            "t".into(),
            "c".into(),
            std::env::temp_dir(),
            token,
            tx,
        );
        assert_eq!(
            NotifyUser
                .call(
                    context,
                    json!({"title":"Ready","message":"Review complete"})
                )
                .await
                .unwrap_err()
                .code,
            "cancelled"
        );
        assert!(rx.recv().await.is_none());
    }
}
