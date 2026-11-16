//! Configuration wiring only; no Azure login, token read, or network access.
use openworker_server_reference::ArtifactConfig;
use serde_json::json;

#[test]
fn azure_artifacts_use_explicit_federated_identity_and_fixed_account_endpoint() {
    let config = json!({
        "kind":"azure", "deployment":"azure-test", "account":"owteststorage", "container":"artifacts",
        "tenant_id":"11111111-1111-4111-8111-111111111111",
        "client_id":"22222222-2222-4222-8222-222222222222",
        "federated_token_file":"/nonexistent/azure-config-test"
    });
    let store = serde_json::from_value::<ArtifactConfig>(config.clone())
        .unwrap()
        .build()
        .unwrap();
    assert_eq!(store.binding().deployment, "azure-test");
    for key in [
        "endpoint",
        "client_secret",
        "access_key",
        "sas_token",
        "authority_host",
    ] {
        let mut invalid = config.clone();
        invalid[key] = "unsupported".into();
        assert!(serde_json::from_value::<ArtifactConfig>(invalid).is_err());
    }
    let mut other = config.clone();
    other["container"] = "other-artifacts".into();
    let other = serde_json::from_value::<ArtifactConfig>(other)
        .unwrap()
        .build()
        .unwrap();
    assert_ne!(store.binding(), other.binding());
}
