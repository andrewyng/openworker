#![cfg(all(feature = "test-support", unix))]
use openworker_sdk::{agents::*, *};
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};
fn plan(steps: Value) -> String {
    format!("TEST_RESOURCE_GRANTS {steps}")
}
async fn history(h: &Harness, s: &Session) -> Value {
    h.client()
        .request("history/read", json!({"session_id":s.id(),"after":0}))
        .await
        .unwrap()
}
#[cfg(target_os = "linux")]
async fn output(h: &Harness, s: &Session) -> Value {
    let snap = history(h, s).await;
    let item = snap["items"]
        .as_array()
        .unwrap()
        .iter()
        .rev()
        .map(|m| &m["item"])
        .find(|m| m["type"] == "function_call_output")
        .unwrap();
    let wrapper: Value = serde_json::from_str(item["output"].as_str().unwrap()).unwrap();
    assert_ne!(wrapper["isError"], true, "{wrapper}");
    serde_json::from_str(wrapper["content"][0]["text"].as_str().unwrap()).unwrap()
}
async fn exercise(managed: bool) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let workspace = root.join("work");
    std::fs::create_dir(&workspace).unwrap();
    let exe = PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let make = || {
        Harness::builder()
            .state_dir(root.join("state"))
            .executable(&exe)
            .sandbox(Sandbox::Off)
            .scripted(true)
    };
    let h = if managed {
        make()
            .managed(exe.clone(), RestartPolicy::default())
            .await
            .unwrap()
    } else {
        make().start().await.unwrap()
    };
    let definition = Agent::new("coder")
        .instructions("Code.")
        .auto_skip_questions(true)
        .approvals(Approvals::Never)
        .tools([
            "coding.write_file",
            "coding.read_file",
            "coding.process_start",
            "coding.process_status",
            "coding.process_stop",
        ]);
    let a = h.create_agent(definition.clone()).await.unwrap();
    let s = a.new_session_in(&workspace).await.unwrap();
    let r = s
        .run(&plan(json!([
            {"name":"ask_user","arguments":{"question":"Which color?","options":["blue","green"]}},
            {"name":"coding__write_file","arguments":{"path":"proof.txt","content":"built"}}
        ])))
        .await
        .unwrap();
    assert_eq!(r.status, RunStatus::Completed, "{r:?}");
    assert_eq!(
        std::fs::read_to_string(workspace.join("proof.txt")).unwrap(),
        "built"
    );
    let snap = s.snapshot().await.unwrap();
    assert_eq!(snap["session"]["permissions"]["auto_skip_questions"], true);
    assert!(history(&h, &s)
        .await
        .to_string()
        .contains("No human answer is available"));
    // The session override changes questions only; bypass still does not answer.
    let ask = a
        .new_session_with(
            SessionOptions::default()
                .workspace(&workspace)
                .auto_skip_questions(false),
        )
        .await
        .unwrap();
    assert_eq!(
        ask.run(&plan(
            json!([{"name":"ask_user","arguments":{"question":"Which color?","options":["blue"]}}])
        ))
        .await
        .unwrap()
        .status,
        RunStatus::Interrupted
    );
    ask.cancel().await.unwrap();
    #[cfg(target_os = "linux")]
    let process = {
        // The actual runner must remain alive across completed turns.
        assert_eq!(s.run(&plan(json!([{"name":"coding__process_start","arguments":{"command":"sleep 120","timeout_seconds":180}}]))).await.unwrap().status,RunStatus::Completed);
        let process = output(&h, &s).await["process_id"].clone();
        assert_eq!(
            s.run(&plan(
                json!([{"name":"coding__process_status","arguments":{"process_id":process}}])
            ))
            .await
            .unwrap()
            .status,
            RunStatus::Completed
        );
        assert_eq!(output(&h, &s).await["status"], "running");
        process
    };
    let reference = s.reference().clone();
    h.shutdown().await.unwrap();
    let h = if managed {
        make()
            .managed(exe.clone(), RestartPolicy::default())
            .await
            .unwrap()
    } else {
        make().start().await.unwrap()
    };
    let a = h.create_agent(definition).await.unwrap();
    let s = a.resume_session(reference).await.unwrap();
    assert_eq!(
        s.snapshot().await.unwrap()["session"]["permissions"]["auto_skip_questions"],
        true
    );
    #[cfg(target_os = "linux")]
    {
        s.run(&plan(
            json!([{"name":"coding__process_status","arguments":{"process_id":process}}]),
        ))
        .await
        .unwrap();
        assert!(history(&h, &s)
            .await
            .to_string()
            .contains("Unknown process"));
    }
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn embedded_coding_and_question_policy() {
    tokio::time::timeout(Duration::from_secs(120), exercise(false))
        .await
        .unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn managed_coding_and_question_policy() {
    tokio::time::timeout(Duration::from_secs(120), exercise(true))
        .await
        .unwrap();
}
