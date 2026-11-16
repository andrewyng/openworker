//! Optional bounded live-model team: real generated artifact and verifier reads.
use super::*;
use openworker_sdk::service::{AgentTeam, Service, Session};
use openworker_store_postgres::Config as DbConfig;
use tokio::io::AsyncReadExt;
pub(super) async fn qualify(
    service: &Service,
    lead: &Session,
    team: &AgentTeam,
    base: &str,
    config: &Config,
    db: &DbConfig,
    root: &Path,
) {
    let task="Build a tiny single-file counter web app using a coding team. Use team_spawn to create agent_type=builder, agent_id=app-builder. Tell it to create index.html in its assigned checkout: a heading, a button and a visible count that increments on click; no external dependencies. It must read proof.txt first, write the HTML, inspect its contents, commit only index.html with a local fixture author, then call team_submit_code for the exact commit and a short summary. Wait for a ready immutable submission ID using team_list_submissions (do not resubmit under new IDs). Then spawn agent_type=builder, agent_id=app-verifier, with source.kind=submission and that exact ID. Ask it to use coding.read_file to read index.html and proof.txt, check the counter structure and no external dependencies, and call team_report_verification with its findings and limitations; do not modify files or claim a browser test. Use team_wait_agent and the inbox to wait as needed. Finish only when the submission has that verifier's report, and tell me its ID and commit. Do not push, publish, integrate, create a PR, access credentials or contact any remote repository. You already have the approved source snapshot. Use tools in the supplied catalog.";
    let run = lead.send(task, Some("live-counter-team")).await.unwrap();
    let result = run.result().await.unwrap();
    assert_eq!(
        result.status, "completed",
        "Live team did not complete: {}",
        result.status
    );
    let rows = team.submissions(None, 20).await.unwrap();
    let row = rows
        .as_array()
        .unwrap()
        .iter()
        .find(|r| {
            r["status"] == "ready"
                && r["reports"].as_array().is_some_and(|a| {
                    a.iter()
                        .any(|p| p["reporter"]["agent_id"] == "app-verifier")
                })
        })
        .expect("Missing ready submitted revision and exact verifier report");
    let id = row["id"].as_str().unwrap();
    let doc: openworker_runtime::agent_team::Document = tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        serde_json::from_value(pg.query_one(&format!("SELECT data FROM {}.agent_teams WHERE data->'roster'->'instances'->(data->>'lead')->>'session_id'=$1",db.schema),&[&lead.id()]).unwrap().get(0)).unwrap()
    });
    let state = &doc.submissions[id];
    let manifest = state.manifest.as_ref().unwrap();
    manifest.validate().unwrap();
    assert_ne!(manifest.submitted_commit, base);
    assert!(doc.publications.is_empty() && doc.integrations.is_empty());
    let artifact = PostgresStore::open(db.clone())
        .unwrap()
        .artifact(
            &manifest.artifact.session_id,
            &manifest.artifact.artifact_id,
        )
        .unwrap();
    let backend = config.artifacts.as_ref().unwrap().build().unwrap();
    let stream = backend
        .get(
            &artifact,
            artifact.proof.as_ref().unwrap(),
            tokio_util::sync::CancellationToken::new(),
        )
        .await
        .unwrap();
    use futures_util::TryStreamExt;
    let mut reader = tokio_util::io::StreamReader::new(stream.map_err(std::io::Error::other));
    let mut bytes = vec![];
    reader.read_to_end(&mut bytes).await.unwrap();
    assert_eq!(bytes.len() as u64, manifest.artifact.size);
    assert_eq!(
        openworker_runtime::bundle::digest(&bytes),
        manifest.artifact.sha256
    );
    let path = root.join("live.bundle");
    std::fs::write(&path, bytes).unwrap();
    let original = tokio::task::block_in_place(|| {
        let mut pg = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        serde_json::from_value::<openworker_store_postgres::service_team_submissions::Intent>(
            pg.query_one(
                &format!(
                    "SELECT data FROM {}.service_team_submissions WHERE id=$1",
                    db.schema
                ),
                &[&id],
            )
            .unwrap()
            .get(0),
        )
        .unwrap()
    });
    let bundle = original.bundle.as_ref().unwrap();
    openworker_runtime::coding_handoff::git::validate(
        &path,
        &bundle.request,
        &bundle.advertised_ref,
    )
    .await
    .unwrap();
    let repo = root.join("live-inspect.git");
    git(root, &["init", "--bare", "-q", repo.to_str().unwrap()]);
    git(
        &repo,
        &[
            "fetch",
            "-q",
            path.to_str().unwrap(),
            &format!("{}:refs/openworker/inspect", bundle.advertised_ref),
        ],
    );
    let html = git(
        &repo,
        &["show", &format!("{}:index.html", manifest.submitted_commit)],
    );
    let html = html.to_ascii_lowercase();
    assert!(
        html.contains("<button")
            && html.contains("<script")
            && (html.contains("onclick") || html.contains("addeventlistener"))
    );
    assert!(!html.contains("https://") && !html.contains("http://"));
    let verifier = doc.roster.instance("app-verifier").unwrap();
    let events = service
        .json(
            "GET",
            &format!("/v1/sessions/{}/events/page?after=0", verifier.session_id),
            None,
            None,
        )
        .await
        .unwrap();
    assert!(
        events["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["type"] == "tool_finished"
                && e["data"]["name"] == "coding__read_file"
                && tool_data(e).to_string().contains("<button")),
        "No real verifier HTML read evidence"
    );
    assert_ne!(
        doc.roster.instance("app-builder").unwrap().session_id,
        verifier.session_id
    );
    eprintln!("PASS C7 bounded live model: lead spawned independent builder and exact-submission verifier; artifact digest/Git integrity/actual counter HTML and verifier read-back checked; no publication/integration");
}
fn git(root: &Path, args: &[&str]) -> String {
    let o = std::process::Command::new("/usr/bin/git")
        .current_dir(root)
        .args([
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.file.allow=always",
        ])
        .args(args)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .output()
        .unwrap();
    assert!(o.status.success(), "Trusted artifact inspection failed");
    String::from_utf8(o.stdout).unwrap()
}
