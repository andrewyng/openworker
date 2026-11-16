use openworker_tools::{
    Tool, ToolContext, ToolDescriptor, ToolError, ToolFuture, ToolOutput, ToolRegistry,
};
use serde_json::{json, Value};
use std::io::Write;
struct Probe(std::sync::atomic::AtomicBool);
impl Tool for Probe {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: "sdk.probe".into(),
            version: "1".into(),
            description: "Test fixture, writes a local marker".into(),
            input_schema: json!({"type":"object","properties":{"block":{"type":"boolean"}},"required":["block"],"additionalProperties":false}),
        }
    }
    fn call(&self, ctx: ToolContext, args: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            let mut file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(ctx.workspace.join("proof.txt"))
                .unwrap();
            writeln!(file, "called").unwrap();
            file.sync_all().unwrap();
            if args["block"] == true {
                ctx.cancellation.cancelled().await;
                return Err(ToolError::cancelled());
            }
            Ok(ToolOutput::json(json!({"written":true})))
        })
    }
    fn control(&self, _: &str, p: Value) -> std::result::Result<Value, ToolError> {
        if p["action"] == "enable" {
            self.0.store(true, std::sync::atomic::Ordering::SeqCst);
        }
        // Deliberately broken test-only control to force in-flight pipe loss.
        if p["action"] == "block" {
            let mut file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(p["marker"].as_str().unwrap())
                .unwrap();
            writeln!(file, "started").unwrap();
            file.sync_all().unwrap();
            std::thread::sleep(std::time::Duration::from_secs(10));
        }
        Ok(json!({"enabled":self.0.load(std::sync::atomic::Ordering::SeqCst)}))
    }
    fn revoke(&self, _: &str) {
        self.0.store(false, std::sync::atomic::Ordering::SeqCst);
    }
}
pub fn registry() -> ToolRegistry {
    let mut r = ToolRegistry::default();
    r.register(Probe(Default::default())).unwrap();
    r.register(TeamProbe).unwrap();
    r.register(Burst).unwrap();
    r
}
struct Burst;
impl Tool for Burst {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: "sdk.burst".into(),
            version: "1".into(),
            description: "Test-only paginated progress stream".into(),
            input_schema: json!({"type":"object","properties":{"start":{"type":"integer","minimum":0,"maximum":495}},"required":["start"],"additionalProperties":false}),
        }
    }
    fn call(&self, ctx: ToolContext, args: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            // Respect the production per-tool 64-progress-event limit.
            let start = args["start"].as_u64().unwrap();
            for n in start..start + 55 {
                ctx.progress(
                    "Fixture progress",
                    json!({"n":n,"padding":"x".repeat(8000)}),
                )
                .await?;
            }
            Ok(ToolOutput::json(json!({"count":55})))
        })
    }
}
struct TeamProbe;
impl Tool for TeamProbe {
    fn descriptor(&self) -> ToolDescriptor {
        ToolDescriptor {
            qualified_name: "test.probe".into(),
            version: "1".into(),
            description: "Synthetic team proof writer".into(),
            input_schema: json!({"type":"object","properties":{"action":{"type":"string","enum":["write_once"]}},"required":["action"],"additionalProperties":false}),
        }
    }
    fn call(&self, ctx: ToolContext, _: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            let mut f = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(ctx.workspace.join("team-proof.txt"))
                .unwrap();
            writeln!(f, "TEAM_PROOF").unwrap();
            f.sync_all().unwrap();
            Ok(ToolOutput::json(json!({"written":true})))
        })
    }
}
