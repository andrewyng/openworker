//! Small local diagnostic, not production sizing or a full-agent overhead claim.
use anyhow::{ensure, Result};
use openworker_observability::{sqlite::Sqlite, Recorder, RecordingSink};
use openworker_telemetry::{Capture, ContentPolicy, Operation, Outcome, Telemetry};
use std::{
    path::PathBuf,
    time::{Duration, Instant},
};
fn main() -> Result<()> {
    let dir = PathBuf::from(
        std::env::args()
            .nth(1)
            .ok_or_else(|| anyhow::anyhow!("New output directory required"))?,
    );
    std::fs::create_dir(&dir)?;
    let n = 1000;
    let off = Telemetry::default();
    let at = Instant::now();
    for _ in 0..n {
        off.span(Operation::Tool, Some("probe-session"), None)
            .finish(Outcome::Ok);
    }
    let disabled_ns = at.elapsed().as_nanos();
    let (recorder, done) = Recorder::start(
        Box::new(Sqlite::open(&dir.join("probe.sqlite"))?),
        "local-probe".into(),
        1024,
        30,
    )?;
    let sink = RecordingSink::new(
        recorder.clone(),
        "local-probe".into(),
        "probe".into(),
        None,
        Capture::Off,
    )?;
    let (t, c) = Telemetry::start_with_content(
        sink,
        1024,
        ContentPolicy {
            capture: Capture::Metadata,
            ..Default::default()
        },
    )?;
    let start = Instant::now();
    for _ in 0..n {
        let mut s = t.span(Operation::Tool, Some("probe-session"), None);
        s.metadata("gen_ai.tool.name", "fixture.read_file");
        s.output_bytes(512);
        s.finish(Outcome::Ok);
    }
    let enqueue_ns = start.elapsed().as_nanos();
    ensure!(
        c.shutdown(Duration::from_secs(5)),
        "Telemetry drain incomplete"
    );
    ensure!(
        done.shutdown(Duration::from_secs(5)),
        "Recording drain incomplete"
    );
    let elapsed = start.elapsed().as_secs_f64();
    let stats = recorder.statistics().snapshot();
    ensure!(
        stats.persisted == n
            && stats.dropped + stats.rejected + stats.failed_writes + stats.abandoned == 0,
        "Probe lost records"
    );
    println!(
        "{}",
        serde_json::json!({"records":n,"disabled_loop_ns":disabled_ns,"recording_enqueue_ns":enqueue_ns,"recording_total_seconds":elapsed,"records_per_second":n as f64/elapsed,"recording":stats,"exporter":t.snapshot(),"scope":"1000 synthetic tool metadata records, local SQLite, one record/transaction; not full-agent overhead or capacity guidance"})
    );
    Ok(())
}
