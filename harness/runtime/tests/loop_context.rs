//! Exercise the actual sandbox process, including a loss at the approval boundary.
use openworker_runtime::loop_channel::PROTOCOL;
use serde_json::{json, Value};
use std::{process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    time::timeout,
};

struct Peer {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}
impl Peer {
    async fn connect() -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_openworker"))
            .arg("sandbox-loop")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let mut peer = Self {
            input: child.stdin.take().unwrap(),
            output: BufReader::new(child.stdout.take().unwrap()),
            child,
        };
        assert_eq!(peer.read().await, json!({"ready":PROTOCOL}));
        peer.send(&json!({"assign":PROTOCOL,"session":"s","turn":"t"}))
            .await;
        assert_eq!(peer.read().await, json!({"request":"context"}));
        peer
    }
    async fn send(&mut self, v: &Value) {
        let mut bytes = serde_json::to_vec(v).unwrap();
        bytes.push(b'\n');
        self.input.write_all(&bytes).await.unwrap();
        self.input.flush().await.unwrap();
    }
    async fn read(&mut self) -> Value {
        let mut line = String::new();
        timeout(Duration::from_secs(15), self.output.read_line(&mut line))
            .await
            .unwrap()
            .unwrap();
        serde_json::from_str(&line).unwrap()
    }
    async fn request_model(&mut self, seq: u64, attempt: &str) {
        self.send(&json!({"attempt":attempt,"seq":seq,"model":"fixture","scripted":true,"output_limit":256,"reasoning":false,"summary":false,"api":"chat-completions","base_url":"https://unused.invalid/v1"})).await;
    }
    async fn start(&mut self, seq: u64, attempt: &str) -> Value {
        self.request_model(seq, attempt).await;
        let response = self.read().await;
        assert_eq!(response["attempt"], attempt);
        response
    }
}
#[tokio::test]
async fn retry_updates_and_recover_a_parked_loop_from_the_external_snapshot() {
    let user = json!({"role":"user","content":"TEST_RESOURCE_GRANTS [{\"name\":\"read_file\",\"arguments\":{}},{\"name\":\"write_file\",\"arguments\":{}}]"});
    let snapshot =
        json!({"seq":1,"session":"s","turn":"t","instructions":"test","tools":[],"history":[user]});
    let mut peer = Peer::connect().await;
    peer.send(&snapshot).await;
    assert_eq!(peer.read().await, json!({"contextAck":1}));
    let first = peer.start(1, "a1").await;
    let read = first["proposal"][0].clone();
    assert_eq!(read["name"], "read_file");
    // Lost synchronization acknowledgment. The replay must never restart inference.
    peer.send(&snapshot).await;
    assert_eq!(peer.read().await, json!({"contextAck":1}));
    peer.send(&json!({"committed":"a1","cursor":8})).await;
    assert_eq!(peer.read().await, json!({"waitingForTools":"a1"}));
    peer.send(&json!({"toolsSettled":"a1"})).await;
    assert_eq!(peer.read().await, json!({"request":"context"}));
    let read_result =
        json!({"type":"function_call_output","call_id":read["call_id"],"output":"ok"});
    let delta = json!({"seq":2,"base":1,"session":"s","turn":"t","history":[read_result]});
    for _ in 0..2 {
        peer.send(&delta).await;
        assert_eq!(peer.read().await, json!({"contextAck":2}));
    }
    let second = peer.start(2, "a2").await;
    let write = second["proposal"][0].clone();
    assert_eq!(write["name"], "write_file");
    peer.send(&json!({"committed":"a2","cursor":14})).await;
    assert_eq!(peer.read().await, json!({"waitingForTools":"a2"}));
    // Outside has persisted the approval. Lose the process before it can confirm parking.
    peer.child.kill().await.unwrap();
    let mut replacement = Peer::connect().await;
    let result = json!({"type":"function_call_output","call_id":write["call_id"],"output":"approved and executed"});
    replacement
        .send(&json!({"seq":3,"base":2,"session":"s","turn":"t","history":[result]}))
        .await;
    assert_eq!(replacement.read().await, json!({"needSnapshot":0}));
    let mut reset = snapshot;
    reset["seq"] = json!(3);
    reset["history"] = json!([user, read, read_result, write, result]);
    replacement.send(&reset).await;
    assert_eq!(replacement.read().await, json!({"contextAck":3}));
    assert_eq!(replacement.start(3, "a3").await["next"], "finish");
    replacement
        .send(&json!({"committed":"a3","cursor":20}))
        .await;
    assert_eq!(replacement.read().await, json!({"finished":"a3"}));
    assert!(replacement.child.wait().await.unwrap().success());
}

#[tokio::test]
async fn advancing_the_cache_cannot_repeat_a_discarded_model_attempt() {
    let mut peer = Peer::connect().await;
    peer.send(&json!({"session":"s","turn":"t","seq":1,"instructions":"test","tools":[],"history":[{"role":"user","content":"TEST_RESOURCE_GRANTS []"}]})).await;
    assert_eq!(peer.read().await, json!({"contextAck":1}));
    assert_eq!(peer.start(1, "discarded").await["next"], "finish");
    peer.send(&json!({"discarded":"discarded"})).await;
    assert_eq!(peer.read().await, json!({"request":"context"}));
    peer.send(&json!({"session":"s","turn":"t","seq":2,"base":1,"history":[]}))
        .await;
    assert_eq!(peer.read().await, json!({"contextAck":2}));
    peer.request_model(2, "discarded").await;
    assert!(!timeout(Duration::from_secs(15), peer.child.wait())
        .await
        .unwrap()
        .unwrap()
        .success());
    let mut line = String::new();
    assert_eq!(peer.output.read_line(&mut line).await.unwrap(), 0);
}
