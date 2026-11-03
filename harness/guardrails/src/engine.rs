use super::*;
use anyhow::bail;
use futures_util::FutureExt;
use std::panic::AssertUnwindSafe;
use std::time::{Duration, Instant};
#[derive(Clone, Debug)]
pub struct Evaluation {
    pub text: String,
    pub findings: Vec<Finding>,
    pub block: Option<Decision>,
    pub unavailable: bool,
}
async fn bounded<T>(
    ms: u64,
    cancel: &CancellationToken,
    future: impl Future<Output = Result<T>>,
) -> Result<T> {
    let future = AssertUnwindSafe(future).catch_unwind();
    tokio::select! {biased;_=cancel.cancelled()=>bail!("Safety evaluation cancelled"),r=tokio::time::timeout(Duration::from_millis(ms),future)=>r.map_err(|_|anyhow::anyhow!("Safety evaluation timed out"))?.map_err(|_|anyhow::anyhow!("Safety handler panicked"))?}
}
async fn detect(
    rule: &Rule,
    context: &Context,
    models: Option<&dyn ModelDetector>,
    cancel: &CancellationToken,
) -> Result<Vec<Match>> {
    match &rule.detector {
        Detector::Model {
            model,
            instructions,
        } => {
            let driver = models.ok_or_else(|| anyhow::anyhow!("Guard model unavailable"))?;
            let found = bounded(rule.timeout_ms, cancel, async {
                driver
                    .detect(
                        model.clone(),
                        instructions.clone(),
                        context.clone(),
                        cancel.clone(),
                    )
                    .await
            })
            .await?;
            fragments(&context.text, &found)
        }
        detector => detectors::detect(detector, &context.text),
    }
}
fn finding(
    c: &Context,
    name: &str,
    version: &str,
    outcome: &str,
    code: Option<String>,
    count: usize,
    started: Instant,
) -> Finding {
    Finding {
        evaluation_id: c.evaluation_id.clone(),
        phase: c.phase,
        check: name.into(),
        version: version.into(),
        outcome: outcome.into(),
        code,
        match_count: count,
        duration_ms: started.elapsed().as_millis().min(u64::MAX as u128) as u64,
    }
}
fn reject(out: &mut Evaluation, decision: Decision) {
    out.block = Some(decision);
}
pub async fn apply(
    config: &Config,
    registry: &Registry,
    models: Option<&dyn ModelDetector>,
    mut context: Context,
    cancel: CancellationToken,
) -> Result<Evaluation> {
    config.validate()?;
    ensure!(
        context.text.len() <= MAX_TEXT,
        "Text exceeds safety size limit"
    );
    let mut out = Evaluation {
        text: context.text.clone(),
        findings: vec![],
        block: None,
        unavailable: false,
    };
    let rules = config.rules(context.phase, context.tool.as_deref());
    let mut transformed = false;
    for rule in &rules {
        let started = Instant::now();
        context.text = out.text.clone();
        context.matches.clear();
        let matches = match detect(rule, &context, models, &cancel).await {
            Ok(m) => m,
            Err(_) => {
                out.findings
                    .push(unavailable(&context, &rule.name, &rule.version));
                out.unavailable = true;
                return Ok(out);
            }
        };
        let count = matches.len();
        if matches.is_empty() {
            out.findings.push(finding(
                &context,
                &rule.name,
                &rule.version,
                "allow",
                None,
                0,
                started,
            ));
            continue;
        }
        context.matches = matches;
        let decision = match &rule.action {
            Action::Block => Decision::block("CONTENT_BLOCKED"),
            Action::Redact {
                replacement,
                keep_last,
            } => Decision::Replace {
                text: detectors::redact(
                    &context.text,
                    &context.matches,
                    replacement,
                    *keep_last,
                    matches!(&rule.detector, Detector::CreditCard),
                ),
            },
            Action::Callback { handler } => match registry.handler(handler) {
                Ok(h) => match bounded(rule.timeout_ms, &cancel, async {
                    h.call(context.clone(), cancel.clone()).await
                })
                .await
                {
                    Ok(d) if d.validate().is_ok() && !matches!(&d, Decision::Allow) => d,
                    _ => {
                        out.findings
                            .push(unavailable(&context, &rule.name, &rule.version));
                        out.unavailable = true;
                        return Ok(out);
                    }
                },
                Err(_) => {
                    out.findings
                        .push(unavailable(&context, &rule.name, &rule.version));
                    out.unavailable = true;
                    return Ok(out);
                }
            },
        };
        match decision {
            Decision::Allow => out.findings.push(finding(
                &context,
                &rule.name,
                &rule.version,
                "allow",
                None,
                count,
                started,
            )),
            Decision::Replace { text } => {
                ensure!(text.len() <= MAX_TEXT, "Replacement exceeds safety limit");
                out.text = text;
                transformed = true;
                out.findings.push(finding(
                    &context,
                    &rule.name,
                    &rule.version,
                    "redacted",
                    None,
                    count,
                    started,
                ));
            }
            decision @ Decision::Block { .. } => {
                let code = if let Decision::Block { code, .. } = &decision {
                    Some(code.clone())
                } else {
                    None
                };
                out.findings.push(finding(
                    &context,
                    &rule.name,
                    &rule.version,
                    "blocked",
                    code,
                    count,
                    started,
                ));
                reject(&mut out, decision);
                return Ok(out);
            }
        }
    }
    // A later custom replacement must never reintroduce an earlier prohibited
    // value. Revalidate without applying transforms/callback effects again.
    if transformed {
        context.text = out.text.clone();
        context.matches.clear();
        for rule in &rules {
            let started = Instant::now();
            let matches = match detect(rule, &context, models, &cancel).await {
                Ok(m) => m,
                Err(_) => {
                    out.findings
                        .push(unavailable(&context, &rule.name, &rule.version));
                    out.unavailable = true;
                    return Ok(out);
                }
            };
            if !matches.is_empty() {
                out.findings.push(finding(
                    &context,
                    &rule.name,
                    &rule.version,
                    "blocked",
                    Some("SANITIZATION_INCOMPLETE".into()),
                    matches.len(),
                    started,
                ));
                reject(&mut out, Decision::block("SANITIZATION_INCOMPLETE"));
                return Ok(out);
            }
        }
    }
    context.text = out.text.clone();
    context.matches.clear();
    for hook in config.hooks.iter().filter(|h| {
        h.when != HookPhase::OnEvent
            && h.when.applies(context.phase)
            && (h.tools.is_empty() || context.tool.as_ref().is_some_and(|t| h.tools.contains(t)))
    }) {
        let started = Instant::now();
        let result = match registry.handler(&hook.handler) {
            Ok(h) => {
                bounded(hook.timeout_ms, &cancel, async {
                    h.call(context.clone(), cancel.clone()).await
                })
                .await
            }
            Err(e) => Err(e),
        };
        match result {
            Ok(Decision::Allow) => out.findings.push(finding(
                &context,
                &hook.name,
                &hook.version,
                "allow",
                None,
                0,
                started,
            )),
            Ok(decision @ Decision::Block { .. }) if decision.validate().is_ok() => {
                let code = if let Decision::Block { code, .. } = &decision {
                    Some(code.clone())
                } else {
                    None
                };
                out.findings.push(finding(
                    &context,
                    &hook.name,
                    &hook.version,
                    "blocked",
                    code,
                    0,
                    started,
                ));
                reject(&mut out, decision);
                return Ok(out);
            }
            _ => {
                out.findings
                    .push(unavailable(&context, &hook.name, &hook.version));
                out.unavailable = true;
                return Ok(out);
            }
        }
    }
    Ok(out)
}
/// Observer failure is diagnostic, never authority. Caller queues/persists this
/// outside the action path; the action's original outcome remains unchanged.
pub async fn observe(
    hook: &Hook,
    registry: &Registry,
    context: Context,
    cancel: CancellationToken,
) -> bool {
    if hook.when != HookPhase::OnEvent {
        return false;
    }
    match registry.handler(&hook.handler) {
        Ok(h) => bounded(hook.timeout_ms, &cancel, async {
            h.call(context, cancel.clone()).await
        })
        .await
        .is_ok(),
        Err(_) => false,
    }
}
