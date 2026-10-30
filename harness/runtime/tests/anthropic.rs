use openworker_runtime::{
    anthropic::{self, Decoder},
    model::Progress,
    provider::Failure,
};
use serde_json::{json, Value};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::mpsc,
};

fn start() -> Value {
    json!({"type":"message_start","message":{"role":"assistant","content":[],"usage":{"input_tokens":20,"cache_read_input_tokens":10,"cache_creation_input_tokens":5}}})
}
fn tool_events() -> Vec<Value> {
    vec![
        start(),
        json!({"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tool_1","name":"read_file","input":{}}}),
        json!({"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"path\":"}}),
        json!({"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\"report.txt\"}"}}),
        json!({"type":"content_block_stop","index":0}),
        json!({"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":12}}),
        json!({"type":"message_stop"}),
    ]
}
#[test]
fn completed_tool_usage_and_history_roundtrip() {
    let mut d = Decoder::default();
    for e in tool_events() {
        assert!(d.event(&e).unwrap().is_none());
    }
    let result = d.finish().unwrap();
    assert_eq!(result.output[0]["name"], "read_file");
    let usage = result.usage.unwrap();
    assert_eq!(usage.input_tokens, 35);
    assert_eq!(usage.cached_input_tokens, 10);
    assert_eq!(usage.output_tokens, 12);
    let mut history = vec![json!({"role":"user","content":"Read the report"})];
    history.extend(result.output);
    history
        .push(json!({"type":"function_call_output","call_id":"tool_1","output":"report contents"}));
    let request = anthropic::body("claude-test", "Instructions", &history, &[json!({"type":"function","name":"read_file","description":"read","parameters":{"type":"object"}})], 8192, None).unwrap();
    assert_eq!(
        request["messages"][1]["content"][0]["input"]["path"],
        "report.txt"
    );
    assert_eq!(request["messages"][2]["content"][0]["type"], "tool_result");
    assert_eq!(request["tool_choice"]["disable_parallel_tool_use"], true);
    assert_eq!(
        request["tools"][0]["input_schema"],
        json!({"type":"object"})
    );
}
#[test]
fn interrupted_or_failed_responses_never_authorize_partial_tools() {
    let events = tool_events();
    for cut in 0..events.len() {
        let mut d = Decoder::default();
        for e in &events[..cut] {
            d.event(e).unwrap();
        }
        assert!(d.finish().is_err());
    }
    for (reason, kind) in [
        ("max_tokens", Failure::Truncated),
        ("refusal", Failure::SafetyBlocked),
        ("pause_turn", Failure::InvalidResponse),
        ("model_context_window_exceeded", Failure::Truncated),
    ] {
        let mut d = Decoder::default();
        for e in &events[..5] {
            d.event(e).unwrap();
        }
        assert_eq!(d.event(&json!({"type":"message_delta","delta":{"stop_reason":reason},"usage":{"output_tokens":12}})).unwrap_err().kind, kind);
        assert!(d.finish().is_err());
    }
    let mut d = Decoder::default();
    for e in &events {
        d.event(e).unwrap();
    }
    assert!(d
        .event(&json!({"type":"error","error":{"type":"overloaded_error"}}))
        .is_err());
    assert!(d.finish().is_err());
}
#[test]
fn malformed_streams_fail_closed() {
    let events = tool_events();
    let mut malformed = vec![];
    let mut e = events.clone();
    e[3]["delta"]["partial_json"] = json!("broken}");
    malformed.push(e);
    let mut e = events.clone();
    e[1]["index"] = json!(1);
    malformed.push(e);
    let mut e = events.clone();
    e.insert(2, start());
    malformed.push(e);
    let mut e = events.clone();
    e.remove(4);
    malformed.push(e);
    let mut e = events.clone();
    e[2]["delta"]["type"] = json!("text_delta");
    malformed.push(e);
    let mut e = events.clone();
    e[5]["delta"]["stop_reason"] = json!("end_turn");
    malformed.push(e);
    for seq in malformed {
        let mut d = Decoder::default();
        let failed = seq.iter().any(|e| d.event(e).is_err());
        assert!(failed || d.finish().is_err());
    }
}
#[test]
fn private_thinking_is_retained_but_not_streamed_and_foreign_history_is_rejected() {
    let mut d = Decoder::default();
    d.event(&start()).unwrap();
    for e in [
        json!({"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}),
        json!({"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"PRIVATE_REASONING"}}),
        json!({"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"opaque-signature"}}),
        json!({"type":"content_block_stop","index":0}),
        json!({"type":"content_block_start","index":1,"content_block":{"type":"text","text":"Public answer"}}),
        json!({"type":"content_block_stop","index":1}),
        json!({"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":12}}),
        json!({"type":"message_stop"}),
    ] {
        assert!(d.event(&e).unwrap().is_none());
    }
    let result = d.finish().unwrap();
    assert_eq!(result.output[0]["type"], "reasoning");
    let mut history = vec![json!({"role":"user","content":"Question"})];
    history.extend(result.output);
    history.push(json!({"role":"user","content":"Follow up"}));
    let b = anthropic::body("test", "system", &history, &[], 1024, None).unwrap();
    assert_eq!(
        b["messages"][1]["content"][0]["signature"],
        "opaque-signature"
    );
    history[1] = json!({"type":"reasoning","encrypted_content":"OPENAI_ONLY"});
    assert!(anthropic::body("test", "system", &history, &[], 1024, None).is_err());
}
#[test]
fn input_pairing_images_denials_and_structured_reviewer_format() {
    let history = vec![
        json!({"role":"user","content":"task"}),
        json!({"type":"function_call","call_id":"one","name":"a","arguments":"{}"}),
        json!({"type":"function_call","call_id":"two","name":"b","arguments":"{}"}),
        json!({"type":"function_call_output","call_id":"one","output":"{\"denied\":true}"}),
        json!({"type":"function_call_output","call_id":"two","output":[{"type":"input_text","text":"capture"},{"type":"input_image","image_url":"data:image/png;base64,YQ=="}]}),
    ];
    let b = anthropic::body(
        "test",
        "system",
        &history,
        &[],
        1024,
        Some(
            &json!({"type":"json_schema","schema":{"type":"object"},"name":"review","strict":true}),
        ),
    )
    .unwrap();
    assert_eq!(b["messages"][2]["content"].as_array().unwrap().len(), 2);
    assert_eq!(b["messages"][2]["content"][0]["is_error"], true);
    assert_eq!(
        b["messages"][2]["content"][1]["content"][1]["source"]["type"],
        "base64"
    );
    assert_eq!(
        b["output_config"]["format"],
        json!({"type":"json_schema","schema":{"type":"object"}})
    );
    assert!(anthropic::body("test", "system", &history[..3], &[], 1024, None).is_err());
    let mut bad = history;
    bad[3]["call_id"] = json!("foreign");
    assert!(anthropic::body("test", "system", &bad, &[], 1024, None).is_err());
}
#[tokio::test]
async fn actual_http_sse_headers_and_terminal_completion() {
    let server = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", server.local_addr().unwrap());
    let response = tool_events()
        .iter()
        .map(|e| format!("data: {e}\n\n"))
        .collect::<String>();
    let task = tokio::spawn(async move {
        let (mut socket, _) = server.accept().await.unwrap();
        let mut raw = vec![];
        let mut block = [0; 1024];
        while !raw.windows(4).any(|w| w == b"\r\n\r\n") {
            let n = socket.read(&mut block).await.unwrap();
            assert!(n > 0);
            raw.extend_from_slice(&block[..n]);
        }
        let header = String::from_utf8(raw).unwrap().to_ascii_lowercase();
        assert!(header.contains("x-api-key: test-only"));
        assert!(header.contains("anthropic-version: 2023-06-01"));
        assert!(!header.contains("authorization: bearer"));
        socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",response.len(),response).as_bytes()).await.unwrap();
    });
    let (tx, mut rx) = mpsc::channel(8);
    anthropic::request(&reqwest::Client::new(), &url, "test-only", json!({}), tx)
        .await
        .unwrap();
    assert!(matches!(rx.recv().await, Some(Progress::Usage(_))));
    assert!(matches!(rx.recv().await, Some(Progress::OutputStarted)));
    assert!(matches!(rx.recv().await, Some(Progress::Usage(_))));
    assert!(matches!(rx.recv().await, Some(Progress::Complete(_))));
    task.await.unwrap();
}

// Anthropic can close an incomplete JSON/thinking block before sending the
// message-level max_tokens reason. Never execute it, but preserve that reason
// so the runtime can take its bounded continuation path.
#[test]
fn truncated_blocks_preserve_message_stop_reason() {
    for block in ["tool_use", "thinking"] {
        for reason in ["max_tokens", "model_context_window_exceeded"] {
            let mut d = Decoder::default();
            d.event(&start()).unwrap();
            let initial = if block == "tool_use" {
                json!({"type":"tool_use","id":"partial","name":"write_file","input":{}})
            } else {
                json!({"type":"thinking","thinking":""})
            };
            d.event(&json!({"type":"content_block_start","index":0,"content_block":initial}))
                .unwrap();
            let delta = if block == "tool_use" {
                json!({"type":"input_json_delta","partial_json":"{\"content\":\"unfinished"})
            } else {
                json!({"type":"thinking_delta","thinking":"synthetic reasoning"})
            };
            d.event(&json!({"type":"content_block_delta","index":0,"delta":delta}))
                .unwrap();
            d.event(&json!({"type":"content_block_stop","index":0}))
                .expect("block validation must wait for the message outcome");
            let err = d.event(&json!({"type":"message_delta","delta":{"stop_reason":reason},"usage":{"output_tokens":8192}})).unwrap_err();
            assert_eq!(err.kind, Failure::Truncated);
            assert!(
                d.finish().is_err(),
                "no partial block may become a completion"
            );
        }
    }
}

#[test]
fn successful_stop_still_rejects_invalid_tool_json_and_unsigned_thinking() {
    for initial in [
        json!({"type":"tool_use","id":"partial","name":"write_file","input":{}}),
        json!({"type":"thinking","thinking":"synthetic reasoning"}),
    ] {
        let is_tool = initial["type"] == "tool_use";
        let mut d = Decoder::default();
        d.event(&start()).unwrap();
        d.event(&json!({"type":"content_block_start","index":0,"content_block":initial}))
            .unwrap();
        if is_tool {
            d.event(&json!({"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"content\":\"unfinished"}})).unwrap();
        }
        d.event(&json!({"type":"content_block_stop","index":0}))
            .unwrap();
        let end = d.event(&json!({"type":"message_delta","delta":{"stop_reason":if is_tool {"tool_use"} else {"end_turn"}},"usage":{"output_tokens":10}}));
        assert!(
            end.is_err() || {
                d.event(&json!({"type":"message_stop"})).unwrap();
                matches!(d.finish(), Err(e) if e.kind == Failure::InvalidResponse)
            }
        );
    }
}

#[test]
fn real_anthropic_token_limit_can_omit_block_stop() {
    // Structural reproduction of a live Claude Sonnet 5 response with a forced
    // tool and max_tokens=64. The provider ended with incomplete tool JSON and
    // message_delta(max_tokens), without a content_block_stop event.
    for reason in ["max_tokens", "model_context_window_exceeded"] {
        let mut d = Decoder::default();
        for e in [
            start(),
            json!({"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"partial","name":"write_file","input":{}}}),
            json!({"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"path\":\"sample.txt\",\"content\":\""}}),
        ] {
            d.event(&e).unwrap();
        }
        assert_eq!(d.event(&json!({"type":"message_delta","delta":{"stop_reason":reason},"usage":{"output_tokens":64}})).unwrap_err().kind, Failure::Truncated);
        assert!(d.finish().is_err());
    }
}
