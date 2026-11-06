use openworker_runtime::connections::{Choice, Manager};
use serde_json::json;
fn key(id: &str, secret: &str, persistence: &str) -> serde_json::Value {
    json!({"id":id,"label":"Test connection","provider":"openai","api_key":secret,"persistence":persistence})
}
#[test]
fn memory_keys_are_not_persisted_or_listed() {
    let root = tempfile::tempdir().unwrap();
    let m = Manager::open(root.path()).unwrap();
    m.set(key("test", "secret-for-test-only", "memory"))
        .unwrap();
    let chosen = m
        .resolve(&Choice {
            connection: "test".into(),
            model: "gpt-6-sol".into(),
        })
        .unwrap();
    assert_eq!(&**m.key(&chosen).unwrap(), "secret-for-test-only");
    assert!(!m
        .list()
        .unwrap()
        .to_string()
        .contains("secret-for-test-only"));
    drop(m);
    let bytes = std::fs::read(root.path().join("model-connections.sqlite")).unwrap();
    assert!(!bytes.windows(20).any(|w| w == b"secret-for-test-only"));
    let m = Manager::open(root.path()).unwrap();
    assert!(m.key(&chosen).is_err());
    assert_eq!(m.list().unwrap()["connections"][0]["configured"], false);
}
#[test]
fn rotation_preserves_identity_but_removal_and_recreation_does_not() {
    let root = tempfile::tempdir().unwrap();
    let m = Manager::open(root.path()).unwrap();
    m.set(key("test", "secret-one", "memory")).unwrap();
    let chosen = m
        .resolve(&Choice {
            connection: "test".into(),
            model: "gpt-6-sol".into(),
        })
        .unwrap();
    m.set(key("test", "secret-two", "memory")).unwrap();
    assert_eq!(&**m.key(&chosen).unwrap(), "secret-two");
    m.remove("test").unwrap();
    assert!(m.key(&chosen).is_err());
    m.set(key("test", "secret-three", "memory")).unwrap();
    assert!(m.key(&chosen).is_err());
}
#[test]
fn defaults_are_separate_validated_and_durable() {
    let root = tempfile::tempdir().unwrap();
    let m = Manager::open(root.path()).unwrap();
    m.set(key("test", "secret-one", "memory")).unwrap();
    m.set_defaults(json!({"agent":{"connection":"test","model":"gpt-6-sol"},"reviewer":{"connection":"test","model":"gpt-6-astra"}})).unwrap();
    drop(m);
    let m = Manager::open(root.path()).unwrap();
    assert_eq!(m.defaults().unwrap().reviewer.unwrap().model, "gpt-6-astra");
    assert!(m
        .set_defaults(
            json!({"agent":{"connection":"missing","model":"valid-model"},"reviewer":null})
        )
        .is_err());
    assert!(Choice {
        connection: "test".into(),
        model: "bad model".into()
    }
    .validate()
    .is_err());
}
#[test]
fn malformed_secret_requests_have_sanitized_errors() {
    let root = tempfile::tempdir().unwrap();
    let m = Manager::open(root.path()).unwrap();
    let err = m
        .set(json!({"api_key":"DO-NOT-ECHO-secret"}))
        .unwrap_err()
        .to_string();
    assert!(!err.contains("DO-NOT-ECHO"));
}

#[test]
fn anthropic_connection_is_pinned_and_cannot_be_retyped_or_cross_wired() {
    use openworker_runtime::provider::Kind;
    let root = tempfile::tempdir().unwrap();
    let m = Manager::open(root.path()).unwrap();
    let mut v = key("claude", "anthropic-test-key", "memory");
    v["provider"] = json!("anthropic");
    m.set(v.clone()).unwrap();
    let selected = m
        .resolve(&Choice {
            connection: "claude".into(),
            model: "claude-test".into(),
        })
        .unwrap();
    assert_eq!(selected.provider, Kind::Anthropic);
    assert_eq!(m.list().unwrap()["connections"][0]["provider"], "anthropic");
    v["provider"] = json!("openai");
    assert!(m.set(v).is_err());
    let mut changed = selected.clone();
    changed.provider = Kind::Openai;
    assert!(m.key(&changed).is_err());
    drop(m);
    let m = Manager::open(root.path()).unwrap();
    assert!(m.key(&selected).is_err());
    let mut v = key("claude", "new-anthropic-key", "memory");
    v["provider"] = json!("anthropic");
    m.set(v).unwrap();
    assert_eq!(&**m.key(&selected).unwrap(), "new-anthropic-key");
}

#[test]
fn server_connections_share_identity_across_nodes_without_persisting_keys() {
    use openworker_runtime::provider::Kind;
    let a = tempfile::tempdir().unwrap();
    let b = tempfile::tempdir().unwrap();
    let ma = Manager::open(a.path()).unwrap();
    let mb = Manager::open(b.path()).unwrap();
    ma.register_host_reference("claude", Kind::Anthropic, "deployment:connection-v1")
        .unwrap();
    let reference = ma
        .resolve(&Choice {
            connection: "claude".into(),
            model: "claude-test".into(),
        })
        .unwrap();
    assert!(ma.key(&reference).is_err());
    ma.register_host(
        "claude",
        Kind::Anthropic,
        "deployment:connection-v1",
        "test-key-one".to_owned().into(),
    )
    .unwrap();
    let selection = ma
        .resolve(&Choice {
            connection: "claude".into(),
            model: "claude-test".into(),
        })
        .unwrap();
    assert_eq!(selection.identity, reference.identity);
    mb.register_host(
        "claude",
        Kind::Anthropic,
        "deployment:connection-v1",
        "test-key-two".to_owned().into(),
    )
    .unwrap();
    assert_eq!(&**mb.key(&selection).unwrap(), "test-key-two");
    mb.register_host(
        "claude",
        Kind::Anthropic,
        "deployment:connection-v2",
        "test-key-three".to_owned().into(),
    )
    .unwrap();
    assert!(mb.key(&selection).is_err());
    assert!(mb
        .register_host(
            "claude",
            Kind::Openai,
            "deployment:connection-v1",
            "test-key-four".to_owned().into()
        )
        .is_err());
    drop(ma);
    drop(mb);
    for p in [a.path(), b.path()] {
        let bytes = std::fs::read(p.join("model-connections.sqlite")).unwrap();
        assert!(!bytes.windows(8).any(|w| w == b"test-key"));
    }
}
#[cfg(not(any(windows, target_os = "macos")))]
#[test]
fn no_plaintext_fallback() {
    let root = tempfile::tempdir().unwrap();
    let m = Manager::open(root.path()).unwrap();
    assert!(m.set(key("test", "secret-one", "secure")).is_err());
    assert_eq!(m.list().unwrap()["connections"], json!([]));
}
#[cfg(windows)]
#[test]
fn windows_secure_roundtrip_no_plaintext_and_scope_binding() {
    let root = tempfile::tempdir().unwrap();
    let m = Manager::open(root.path()).unwrap();
    m.set(key("test", "secret-for-test-only", "secure"))
        .unwrap();
    let chosen = m
        .resolve(&Choice {
            connection: "test".into(),
            model: "gpt-6-sol".into(),
        })
        .unwrap();
    drop(m);
    let bytes = std::fs::read(root.path().join("model-connections.sqlite")).unwrap();
    assert!(!bytes.windows(20).any(|w| w == b"secret-for-test-only"));
    let m = Manager::open(root.path()).unwrap();
    assert_eq!(&**m.key(&chosen).unwrap(), "secret-for-test-only");
    drop(m);
    let other = tempfile::tempdir().unwrap();
    std::fs::copy(
        root.path().join("model-connections.sqlite"),
        other.path().join("model-connections.sqlite"),
    )
    .unwrap();
    let m = Manager::open(other.path()).unwrap();
    assert!(m.key(&chosen).is_err());
}

#[test]
fn provider_endpoint_is_immutable_and_local_models_need_no_fake_key() {
    let root = tempfile::tempdir().unwrap();
    let m = Manager::open(root.path()).unwrap();
    let config =
        json!({"name":"Ollama","api":"chat-completions","base_url":"http://127.0.0.1:11434/v1"});
    assert!(m
        .set_provider(json!({"name":"LocalMissingUrl","api":"chat-completions"}))
        .is_err());
    m.set_provider(config.clone()).unwrap();
    assert!(m
        .register_host(
            "Ollama",
            openworker_runtime::provider::Kind::Openai,
            "deployment",
            zeroize::Zeroizing::new("test-only-key".into())
        )
        .is_err());
    let selected = m.resolve(&Choice::new("Ollama", "org/model:8b")).unwrap();
    assert!(m.key(&selected).unwrap().is_empty());
    assert_eq!(
        m.endpoint(&selected).unwrap().1,
        "http://127.0.0.1:11434/v1/chat/completions"
    );
    assert_eq!(m.list().unwrap()["connections"][0]["configured"], true);
    let mut changed = config.clone();
    changed["base_url"] = json!("https://different.example/v1");
    assert!(m.set_provider(changed).is_err());
    let mut changed = config.clone();
    changed["api"] = json!("responses");
    assert!(m.set_provider(changed).is_err());
    let mut rotated = config.clone();
    rotated["credentials"] = json!({"api_key":"new-test-key","storage":"memory"});
    m.set_provider(rotated).unwrap();
    assert_eq!(&**m.key(&selected).unwrap(), "new-test-key");
    drop(m);
    let m = Manager::open(root.path()).unwrap();
    assert!(m.key(&selected).is_err());
    m.set_provider(config.clone()).unwrap();
    assert!(m.key(&selected).is_err()); // Missing memory key is not silently removed.
    m.remove("Ollama").unwrap();
    m.set_provider(config).unwrap();
    assert!(m.endpoint(&selected).is_err());
}

#[cfg(target_os = "macos")]
#[test]
#[ignore = "Explicit qualification: writes a disposable macOS Keychain item"]
fn macos_keychain_roundtrip_and_removal() {
    let root = tempfile::tempdir().unwrap();
    let m = Manager::open(root.path()).unwrap();
    m.set(key("test", "keychain-synthetic-test-key", "secure"))
        .unwrap();
    let selected = m.resolve(&Choice::new("test", "model")).unwrap();
    drop(m);
    let m = Manager::open(root.path()).unwrap();
    let result = m.key(&selected).unwrap();
    m.remove("test").unwrap();
    assert_eq!(&**result, "keychain-synthetic-test-key");
    let bytes = std::fs::read(root.path().join("model-connections.sqlite")).unwrap();
    assert!(!bytes
        .windows(b"keychain-synthetic-test-key".len())
        .any(|w| w == b"keychain-synthetic-test-key"));
    assert!(m.key(&selected).is_err());
}
