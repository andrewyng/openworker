//! Explicitly invoked, user-authorized demo. Not a macOS harness implementation.
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    #[cfg(target_os = "macos")]
    {
        use openworker_tools::{Tool, ToolContext};
        let (tx, _rx) = tokio::sync::mpsc::channel(4);
        let context = ToolContext::new(
            "demo".into(),
            "demo".into(),
            "demo".into(),
            std::env::current_dir()?,
            Default::default(),
            tx,
        );
        let result = notification_tools::MacDialog.call(context, serde_json::json!({"title":"application SDK test","message":"A tool from the application Rust crate opened this dialog. It closes automatically after five seconds."})).await;
        println!("{}", serde_json::to_string(&result)?);
        anyhow::ensure!(result.is_ok(), "Dialog demo failed");
    }
    #[cfg(not(target_os = "macos"))]
    anyhow::bail!("This standalone dialog demo requires macOS");
    #[allow(unreachable_code)]
    Ok(())
}
