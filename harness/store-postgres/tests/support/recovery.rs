//! Explicit synthetic domain fixtures in an ISOLATED restored copy, never a worker.
//! No tools, provider calls, source database mutations or secret output.
use anyhow::{ensure, Result};
use openworker_runtime::store::{Session, StateStore};
use openworker_store_postgres::{
    auth::{Role, Scope, Spec},
    Config, PostgresStore, Tls,
};
use serde_json::json;
fn main() -> Result<()> {
    ensure!(
        std::env::var("OW_RECOVERY_FIXTURE").as_deref() == Ok("isolated-copy"),
        "Explicit isolated-copy marker required"
    );
    let mut c = Config::new(std::env::var("OW_SERVER_DATABASE_URL")?, "ow_helm".into());
    c.tls = Tls::Verified {
        ca_file: Some("/private/pg.ca.crt".into()),
    };
    let mut store = PostgresStore::open(c)?;
    match std::env::args().nth(1).as_deref() {
        Some("seed") => {
            for kind in ["approval", "question", "unknown"] {
                let sid = format!("s_backup_{kind}");
                ensure!(
                    store.inspect(&sid).is_err(),
                    "Fixture already exists; never overwrite"
                );
                let mut s: Session = serde_json::from_value(
                    json!({"id":sid,"name":format!("Synthetic backup {kind}"),"workspace":"/sandbox","coworker":"task-desk","bundle_digest":"backup-bundle","schema_digest":"backup-schema","interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()}),
                )?;
                s.tool_policy.digest = "backup-policy".into();
                s.resource_policy.digest = "backup-resources".into();
                store.save(
                    &s,
                    "session_created",
                    json!({"synthetic_recovery_fixture":true}),
                )?;
                let (mut s, _, _) = store.accept(
                    &sid,
                    "backup-submit",
                    "Deterministic domain fixture, no tool executes",
                )?;
                let tid = s.turn.as_ref().unwrap().id.clone();
                let call = json!({"type":"function_call","call_id":format!("backup-{kind}"),"name":if kind=="question" {"ask_user_question"} else {"write_file"},"arguments":"{\"path\":\"do-not-replay.txt\",\"content\":\"fixture\"}"});
                s.history.push(call.clone());
                s.turn.as_mut().unwrap().queue = vec![call.clone()];
                s.turn.as_mut().unwrap().status = if kind == "unknown" {
                    "executing"
                } else {
                    "waiting"
                }
                .into();
                if kind != "unknown" {
                    s.interactions.push(serde_json::from_value(json!({"id":format!("i_backup_{kind}"),"turn_id":tid,"kind":kind,"call":call,"bundle_digest":"backup-bundle","schema_digest":"backup-schema","policy_digest":"backup-policy","resource_policy_digest":"backup-resources","response":null}))?);
                }
                store.save(
                    &s,
                    if kind == "unknown" {
                        "tool_started"
                    } else {
                        "interaction_required"
                    },
                    json!({"call_id":format!("backup-{kind}"),"synthetic_recovery_fixture":true}),
                )?;
                store.release(&sid)?;
            }
            let admin_token = std::fs::read_to_string("/fixture-secrets/admin")?;
            let revoked_token = std::fs::read_to_string("/fixture-secrets/revoked")?;
            let admin = store.authorized(admin_token.trim())?;
            let key = admin.auth_issue(
                "backup-revoked",
                Spec {
                    principal: "backup-revoked".into(),
                    role: Role::Observer,
                    scope: Scope::All,
                    environments: vec![],
                    expires_at: None,
                },
                revoked_token.trim(),
            )?;
            admin.auth_revoke(key["id"].as_str().unwrap())?;
            println!(
                "Synthetic pending approval/question, unknown tool outcome and revoked key seeded"
            );
        }
        Some("recover") => {
            for kind in ["approval", "question", "unknown"] {
                let sid = format!("s_backup_{kind}");
                let before = store.inspect(&sid)?;
                let starts = store
                    .read_events(&sid, 0)?
                    .iter()
                    .filter(|e| e.kind == "tool_started")
                    .count();
                store.claim(&sid)?;
                store.recover()?;
                let after = store.get(&sid)?;
                ensure!(
                    after.turn.as_ref().unwrap().id == before.turn.as_ref().unwrap().id,
                    "Turn identity changed"
                );
                if kind == "unknown" {
                    ensure!(
                        after.turn.as_ref().unwrap().status == "interrupted",
                        "Unknown outcome not interrupted"
                    );
                    ensure!(
                        after.turn.as_ref().unwrap().queue.is_empty(),
                        "Started effect left replayable"
                    );
                    ensure!(
                        after.history.last().unwrap()["output"]
                            .as_str()
                            .unwrap()
                            .contains("tool_outcome_unknown"),
                        "Missing unknown marker"
                    );
                } else {
                    ensure!(
                        serde_json::to_value(&before.interactions)?
                            == serde_json::to_value(&after.interactions)?,
                        "Interaction changed"
                    );
                    ensure!(
                        after.interactions[0].response.is_none(),
                        "Pending interaction auto-answered"
                    );
                    ensure!(
                        after.turn.as_ref().unwrap().status == "recoverable",
                        "Pending interaction not preserved"
                    );
                }
                ensure!(
                    store
                        .read_events(&sid, 0)?
                        .iter()
                        .filter(|e| e.kind == "tool_started")
                        .count()
                        == starts,
                    "Tool replay detected"
                );
                let cursor = store.cursor(&sid)?;
                store.recover()?;
                ensure!(store.cursor(&sid)? == cursor, "Recovery not idempotent");
                store.release(&sid)?;
            }
            println!("Pending identities preserved; unknown outcome interrupted without replay; recovery idempotent");
        }
        _ => anyhow::bail!("Use seed | recover on the isolated drill copy only"),
    }
    Ok(())
}
