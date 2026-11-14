//! cargo run -p openworker-telemetry --features otlp --example collector -- http://127.0.0.1:PORT
#[cfg(feature = "otlp")]
fn main() -> anyhow::Result<()> {
    use openworker_telemetry::*;
    let (telemetry, controller) = otlp::Config {
        deployment: "example".into(),
        endpoint: std::env::args()
            .nth(1)
            .ok_or_else(|| anyhow::anyhow!("Explicit collector endpoint required"))?,
        ..Default::default()
    }
    .start("qualification")?;
    let rt = tokio::runtime::Builder::new_current_thread().build()?;
    rt.block_on(async {
        let root = telemetry.span(Operation::Turn, Some("collector-test"), None);
        scope(root.context(), async {
            let mut model = telemetry.span(Operation::Model, Some("collector-test"), None);
            model.usage(42, 9);
            model.finish(Outcome::Ok);
            telemetry.event(
                Operation::ApprovalRequested,
                Outcome::Waiting,
                Some("collector-test"),
                None,
            );
            telemetry.event(Operation::Tool, Outcome::Ok, Some("collector-test"), None);
        })
        .await;
        root.finish(Outcome::Ok);
    });
    anyhow::ensure!(
        controller.shutdown(std::time::Duration::from_secs(5)),
        "Telemetry drain timed out"
    );
    println!("{}", serde_json::to_string(&controller.snapshot())?);
    Ok(())
}
#[cfg(not(feature = "otlp"))]
fn main() {
    eprintln!("Enable feature otlp");
}
