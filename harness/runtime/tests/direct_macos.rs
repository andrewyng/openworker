#![cfg(target_os = "macos")]
use openworker_runtime::direct;
use serde_json::json;
#[tokio::test]
async fn native_macos_mcp_roundtrip_and_owned_cleanup() {
    let temp = tempfile::tempdir().unwrap();
    let binary = std::path::Path::new(env!("CARGO_BIN_EXE_openworker"));
    for _ in 0..8 {
        let mut mcp = direct::Mcp::launch(
            binary,
            temp.path(),
            &[
                binary.to_string_lossy().into(),
                "mcp-example".into(),
                "--workspace".into(),
                temp.path().to_string_lossy().into(),
            ],
        )
        .await
        .unwrap();
        mcp.start().await.unwrap();
        let result = mcp
            .request(
                "tools/call",
                json!({"name":"example_echo","arguments":{"text":"hello macOS"}}),
            )
            .await
            .unwrap();
        assert_ne!(result["isError"], true);
        mcp.close().await.unwrap();
        direct::cleanup(&mcp.identity).await.unwrap();
    }
}
