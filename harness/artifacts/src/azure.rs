//! Azure Blob artifacts with an explicit AKS workload identity. Configuration is
//! host-owned; no shared keys, SAS, ambient Azure settings or IMDS fallback.
use crate::*;
use object_store::{
    azure::{AzureCredentialProvider, MicrosoftAzure, MicrosoftAzureBuilder},
    client::{HttpClient, HttpConnector},
    ClientOptions, ObjectStore, ObjectStoreExt, PutMode, PutOptions, RetryConfig,
};
use std::path::PathBuf;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub deployment: String,
    pub account: String,
    pub container: String,
    pub tenant_id: String,
    pub client_id: String,
    pub federated_token_file: PathBuf,
}

pub struct Azure {
    client: MicrosoftAzure,
}

// object_store's default HTTP client follows redirects. In particular a 307 PUT
// must never forward a write or a token to a different endpoint. Disable both
// redirect following and transport-level retries, including the OAuth client.
#[derive(Debug)]
struct Connector(reqwest013::Client);
impl HttpConnector for Connector {
    fn connect(&self, _: &ClientOptions) -> object_store::Result<HttpClient> {
        Ok(HttpClient::new(self.0.clone()))
    }
}

fn map(error: object_store::Error) -> anyhow::Error {
    match error {
        object_store::Error::NotFound { .. } => Error::NotFound.into(),
        object_store::Error::AlreadyExists { .. } | object_store::Error::Precondition { .. } => {
            Error::Conflict.into()
        }
        _ => Error::Unavailable.into(),
    }
}

impl Config {
    fn validate(&self) -> Result<()> {
        component(&self.deployment, 64)?;
        ensure!(
            (3..=24).contains(&self.account.len())
                && self
                    .account
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit()),
            Error::Invalid
        );
        ensure!(
            (3..=63).contains(&self.container.len())
                && self
                    .container
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
                && !self.container.starts_with('-')
                && !self.container.ends_with('-')
                && !self.container.contains("--"),
            Error::Invalid
        );
        for id in [&self.tenant_id, &self.client_id] {
            let uuid = uuid::Uuid::parse_str(id).map_err(|_| Error::Invalid)?;
            ensure!(
                uuid.to_string() == id.to_ascii_lowercase() && !uuid.is_nil(),
                Error::Invalid
            );
        }
        // Do not open it here: controllers share configuration but receive no
        // token. Projected token files may be symlinks and rotate in-place.
        ensure!(
            self.federated_token_file.is_absolute()
                && self.federated_token_file.to_str().is_some()
                && !self
                    .federated_token_file
                    .components()
                    .any(|c| matches!(c, std::path::Component::ParentDir)),
            Error::Invalid
        );
        Ok(())
    }

    pub fn build(&self) -> Result<Store> {
        self.build_inner(
            &format!("https://{}.blob.core.windows.net", self.account),
            None,
            false,
            None,
        )
    }

    fn build_inner(
        &self,
        endpoint: &str,
        credentials: Option<AzureCredentialProvider>,
        fixture_http: bool,
        fixture_authority: Option<&str>,
    ) -> Result<Store> {
        self.validate()?;
        if rustls::crypto::CryptoProvider::get_default().is_none() {
            let _ = rustls::crypto::ring::default_provider().install_default();
        }
        let client = reqwest013::Client::builder()
            .redirect(reqwest013::redirect::Policy::none())
            .retry(reqwest013::retry::never())
            .no_proxy()
            .https_only(!fixture_http)
            .timeout(Duration::from_secs(15))
            .connect_timeout(Duration::from_secs(3))
            .no_gzip()
            .no_brotli()
            .no_zstd()
            .no_deflate()
            .build()
            .map_err(|_| Error::Unavailable)?;
        let mut builder = MicrosoftAzureBuilder::new()
            .with_account(&self.account)
            .with_container_name(&self.container)
            .with_endpoint(endpoint.to_owned())
            .with_tenant_id(&self.tenant_id)
            .with_client_id(&self.client_id)
            .with_federated_token_file(self.federated_token_file.to_str().ok_or(Error::Invalid)?)
            .with_credential_type("workload_identity")
            .with_authority_host(fixture_authority.unwrap_or("https://login.microsoftonline.com"))
            .with_http_connector(Connector(client))
            .with_client_options(ClientOptions::new().with_allow_http(fixture_http))
            // A failed write may have reached storage. Only an explicit retry
            // reconciles identical immutable bytes through the Store contract.
            .with_retry(RetryConfig {
                max_retries: 0,
                retry_timeout: Duration::from_secs(15),
                ..Default::default()
            });
        if let Some(credentials) = credentials {
            builder = builder.with_credentials(credentials);
        }
        let identity = hash(
            format!(
                "azure-chunks-v1:{}:{}:{}:{}:{}:{}",
                self.deployment,
                self.account,
                self.container,
                endpoint,
                self.tenant_id.to_ascii_lowercase(),
                self.client_id.to_ascii_lowercase()
            )
            .as_bytes(),
        );
        Store::new(
            Arc::new(Azure {
                client: builder.build().map_err(|_| Error::Unavailable)?,
            }),
            Binding {
                deployment: self.deployment.clone(),
                identity,
            },
        )
    }
}

#[async_trait::async_trait]
impl Backend for Azure {
    async fn create(&self, key: &str, bytes: Bytes) -> Result<()> {
        ensure!(bytes.len() <= CHUNK, Error::TooLarge);
        let path = object_store::path::Path::parse(key).map_err(|_| Error::Invalid)?;
        self.client
            .put_opts(
                &path,
                bytes.into(),
                PutOptions {
                    mode: PutMode::Create,
                    ..Default::default()
                },
            )
            .await
            .map_err(map)?;
        Ok(())
    }
    async fn read(&self, key: &str, limit: usize) -> Result<Bytes> {
        ensure!(limit <= CHUNK, Error::TooLarge);
        let path = object_store::path::Path::parse(key).map_err(|_| Error::Invalid)?;
        let result = self.client.get(&path).await.map_err(map)?;
        ensure!(result.meta.size <= limit as u64, Error::Conflict);
        let mut input = result.into_stream();
        let mut bytes = Vec::with_capacity(limit.min(CHUNK));
        while let Some(chunk) = input.next().await {
            let chunk = chunk.map_err(map)?;
            ensure!(bytes.len() + chunk.len() <= limit, Error::Conflict);
            bytes.extend_from_slice(&chunk);
        }
        Ok(bytes.into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        body::to_bytes,
        extract::{Request, State},
        http::{header, StatusCode},
        response::{IntoResponse, Response},
        routing::any,
        Json, Router,
    };
    use futures_util::TryStreamExt;
    use object_store::{azure::AzureCredential, StaticCredentialProvider};
    use std::{
        collections::HashMap,
        sync::{
            atomic::{AtomicUsize, Ordering},
            Mutex,
        },
    };

    #[derive(Clone, Default)]
    struct Fixture {
        objects: Arc<Mutex<HashMap<String, Bytes>>>,
        calls: Arc<AtomicUsize>,
        mode: Arc<AtomicUsize>,
        tokens: Arc<AtomicUsize>,
        projected: Arc<Mutex<String>>,
    }
    async fn handle(State(f): State<Fixture>, request: Request) -> Response {
        if request.uri().path().ends_with("/oauth2/v2.0/token") {
            f.tokens.fetch_add(1, Ordering::SeqCst);
            if f.mode.load(Ordering::SeqCst) == 6 {
                return (
                    StatusCode::TEMPORARY_REDIRECT,
                    [(header::LOCATION, "/token-redirected")],
                )
                    .into_response();
            }
            if f.mode.load(Ordering::SeqCst) == 7 {
                return StatusCode::SERVICE_UNAVAILABLE.into_response();
            }
            let body = to_bytes(request.into_body(), 8192).await.unwrap();
            let body = String::from_utf8(body.to_vec()).unwrap();
            assert!(body.contains("grant_type=client_credentials"));
            assert!(body.contains("scope=https%3A%2F%2Fstorage.azure.com%2F.default"));
            assert!(body.contains(&format!("client_assertion={}", f.projected.lock().unwrap())));
            return Json(serde_json::json!({"access_token":"synthetic-access", "expires_in":1, "token_type":"Bearer"})).into_response();
        }
        f.calls.fetch_add(1, Ordering::SeqCst);
        assert_eq!(
            request.headers().get(header::AUTHORIZATION).unwrap(),
            "Bearer synthetic-access"
        );
        match f.mode.load(Ordering::SeqCst) {
            1 => {
                return (
                    StatusCode::TEMPORARY_REDIRECT,
                    [(header::LOCATION, "/redirected")],
                )
                    .into_response()
            }
            2 => return StatusCode::SERVICE_UNAVAILABLE.into_response(),
            3 => return StatusCode::UNAUTHORIZED.into_response(),
            4 => tokio::time::sleep(Duration::from_secs(30)).await,
            _ => (),
        }
        let key = request.uri().path().to_owned();
        if request.method() == axum::http::Method::PUT {
            assert_eq!(request.headers().get(header::IF_NONE_MATCH).unwrap(), "*");
            assert_eq!(
                request.headers().get("x-ms-blob-type").unwrap(),
                "BlockBlob"
            );
            let body = to_bytes(request.into_body(), CHUNK + 1).await.unwrap();
            let mut objects = f.objects.lock().unwrap();
            if objects.contains_key(&key) {
                return StatusCode::PRECONDITION_FAILED.into_response();
            }
            objects.insert(key, body);
            // Storage commits, but the response is lost/unsuccessful. The caller
            // cannot infer whether it is safe to repeat a generating action.
            if f.mode
                .compare_exchange(5, 0, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
            {
                return StatusCode::SERVICE_UNAVAILABLE.into_response();
            }
            return (StatusCode::CREATED, [(header::ETAG, "\"immutable\"")]).into_response();
        }
        let bytes = f.objects.lock().unwrap().get(&key).cloned();
        match bytes {
            Some(bytes) => (
                [
                    (header::ETAG, "\"immutable\""),
                    (header::LAST_MODIFIED, "Wed, 21 Oct 2015 07:28:00 GMT"),
                ],
                bytes,
            )
                .into_response(),
            None => StatusCode::NOT_FOUND.into_response(),
        }
    }
    async fn serve(f: Fixture) -> (String, tokio::task::JoinHandle<()>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let task = tokio::spawn(async move {
            axum::serve(listener, Router::new().fallback(any(handle)).with_state(f))
                .await
                .unwrap()
        });
        (endpoint, task)
    }
    fn config() -> Config {
        Config {
            deployment: "ows-test".into(),
            account: "owteststorage".into(),
            container: "artifacts".into(),
            tenant_id: "11111111-1111-4111-8111-111111111111".into(),
            client_id: "22222222-2222-4222-8222-222222222222".into(),
            federated_token_file: "/nonexistent/azure-test-token".into(),
        }
    }
    fn fixture_store(config: &Config, endpoint: &str) -> Store {
        config
            .build_inner(
                endpoint,
                Some(Arc::new(StaticCredentialProvider::new(
                    AzureCredential::BearerToken("synthetic-access".into()),
                ))),
                true,
                None,
            )
            .unwrap()
    }
    fn artifact(store: &Store, bytes: &[u8]) -> Artifact {
        let id = format!("a_{}", uuid::Uuid::new_v4().simple());
        Artifact {
            id: id.clone(),
            registration: Registration {
                request_id: "request".into(),
                session_id: "s_test".into(),
                turn_id: "t_test".into(),
                name: "sample.bin".into(),
                content_type: "application/octet-stream".into(),
                expected: Expected {
                    size: bytes.len() as u64,
                    sha256: hash(bytes),
                },
            },
            binding: store.binding().clone(),
            key: format!("{}/s_test/{id}", store.binding().deployment),
            status: Status::Pending,
            proof: None,
        }
    }
    fn assert_error<T: std::fmt::Debug>(result: Result<T>, expected: Error) {
        assert_eq!(result.unwrap_err().downcast_ref::<Error>(), Some(&expected));
    }
    #[tokio::test]
    async fn immutable_roundtrip_conflict_hash_scope_and_cancellation() {
        let fixture = Fixture::default();
        let (endpoint, server) = serve(fixture.clone()).await;
        let config = config();
        let store = fixture_store(&config, &endpoint);
        let bytes: Vec<_> = (0..CHUNK * 2 + 17).map(|i| (i % 251) as u8).collect();
        let a = artifact(&store, &bytes);
        let cancel = CancellationToken::new();
        let (one, two) = tokio::join!(
            store.put(&a, bytes.as_slice(), &cancel),
            store.put(&a, bytes.as_slice(), &cancel)
        );
        let proof = one.unwrap();
        assert_eq!(two.unwrap(), proof);
        assert_eq!(
            store
                .get(&a, &proof, cancel.clone())
                .await
                .unwrap()
                .try_collect::<Vec<_>>()
                .await
                .unwrap()
                .concat(),
            bytes
        );
        let mut wrong = bytes.clone();
        wrong[0] ^= 1;
        assert_error(
            store.put(&a, wrong.as_slice(), &cancel).await,
            Error::Conflict,
        );
        assert_eq!(store.verify(&a, &cancel).await.unwrap(), proof);
        for field in ["deployment", "account", "container", "client", "tenant"] {
            let mut other = config.clone();
            match field {
                "deployment" => other.deployment = "other".into(),
                "account" => other.account = "otheraccount".into(),
                "container" => other.container = "other-container".into(),
                "client" => other.client_id = "33333333-3333-4333-8333-333333333333".into(),
                _ => other.tenant_id = "44444444-4444-4444-8444-444444444444".into(),
            }
            let before = fixture.calls.load(Ordering::SeqCst);
            assert_error(
                fixture_store(&other, &endpoint).head(&a, &cancel).await,
                Error::Conflict,
            );
            assert_eq!(fixture.calls.load(Ordering::SeqCst), before);
        }
        let stopped = CancellationToken::new();
        stopped.cancel();
        assert_error(
            store.put(&a, bytes.as_slice(), &stopped).await,
            Error::Cancelled,
        );
        fixture.objects.lock().unwrap().insert(
            format!("/artifacts/{}/part-00", a.key),
            Bytes::from(vec![7; CHUNK]),
        );
        assert_error(store.verify(&a, &cancel).await, Error::Conflict);
        let empty = artifact(&store, b"");
        let proof = store.put(&empty, &b""[..], &cancel).await.unwrap();
        assert_eq!(store.verify(&empty, &cancel).await.unwrap(), proof);
        server.abort();
    }
    #[tokio::test]
    async fn bounded_reads_no_redirect_or_replay_and_inflight_cancel() {
        let fixture = Fixture::default();
        let (endpoint, server) = serve(fixture.clone()).await;
        let store = fixture_store(&config(), &endpoint);
        assert_error(
            store
                .backend
                .create("sample", Bytes::from(vec![0; CHUNK + 1]))
                .await,
            Error::TooLarge,
        );
        fixture
            .objects
            .lock()
            .unwrap()
            .insert("/artifacts/large".into(), Bytes::from(vec![0; 64]));
        assert_error(store.backend.read("large", 63).await, Error::Conflict);
        assert_error(
            store.backend.read("large", CHUNK + 1).await,
            Error::TooLarge,
        );
        assert_error(store.backend.read("missing", CHUNK).await, Error::NotFound);
        for mode in [1, 2, 3] {
            fixture.mode.store(mode, Ordering::SeqCst);
            let before = fixture.calls.load(Ordering::SeqCst);
            assert_error(
                store
                    .backend
                    .create("unknown-outcome", Bytes::from_static(b"data"))
                    .await,
                Error::Unavailable,
            );
            assert_eq!(
                fixture.calls.load(Ordering::SeqCst),
                before + 1,
                "redirect/auth/server failures must not replay writes"
            );
        }
        fixture.mode.store(5, Ordering::SeqCst);
        let interrupted = artifact(&store, b"committed-once");
        let before = fixture.calls.load(Ordering::SeqCst);
        assert_error(
            store
                .put(
                    &interrupted,
                    &b"committed-once"[..],
                    &CancellationToken::new(),
                )
                .await,
            Error::Unavailable,
        );
        assert_eq!(fixture.calls.load(Ordering::SeqCst), before + 1);
        // Explicit resubmission reconciles the existing chunk and publishes a
        // manifest. It does not regenerate bytes or overwrite stored objects.
        store
            .put(
                &interrupted,
                &b"committed-once"[..],
                &CancellationToken::new(),
            )
            .await
            .unwrap();
        fixture.mode.store(4, Ordering::SeqCst);
        let cancel = CancellationToken::new();
        let stop = cancel.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            stop.cancel()
        });
        let a = artifact(&store, b"data");
        assert_error(
            tokio::time::timeout(Duration::from_secs(1), store.put(&a, &b"data"[..], &cancel))
                .await
                .unwrap(),
            Error::Cancelled,
        );
        server.abort();
    }
    #[tokio::test]
    async fn workload_exchange_rereads_rotated_projected_token_without_fallback() {
        let fixture = Fixture::default();
        let (endpoint, server) = serve(fixture.clone()).await;
        let dir = tempfile::tempdir().unwrap();
        let mut config = config();
        config.federated_token_file = dir.path().join("token");
        std::fs::write(&config.federated_token_file, "projected-one").unwrap();
        *fixture.projected.lock().unwrap() = "projected-one".into();
        let store = config
            .build_inner(&endpoint, None, true, Some(&endpoint))
            .unwrap();
        store
            .backend
            .create("one", Bytes::from_static(b"1"))
            .await
            .unwrap();
        std::fs::write(&config.federated_token_file, "projected-two").unwrap();
        *fixture.projected.lock().unwrap() = "projected-two".into();
        tokio::time::sleep(Duration::from_millis(110)).await;
        store
            .backend
            .create("two", Bytes::from_static(b"2"))
            .await
            .unwrap();
        assert_eq!(fixture.tokens.load(Ordering::SeqCst), 2);
        std::fs::write(&config.federated_token_file, "projected-two").unwrap();
        for mode in [6, 7] {
            fixture.mode.store(mode, Ordering::SeqCst);
            let rejected = config
                .build_inner(&endpoint, None, true, Some(&endpoint))
                .unwrap();
            let before = fixture.tokens.load(Ordering::SeqCst);
            assert_error(
                rejected
                    .backend
                    .create("denied", Bytes::from_static(b"3"))
                    .await,
                Error::Unavailable,
            );
            assert_eq!(fixture.tokens.load(Ordering::SeqCst), before + 1);
            assert_eq!(
                fixture.calls.load(Ordering::SeqCst),
                2,
                "failed token exchange must not reach storage"
            );
        }
        std::fs::remove_file(&config.federated_token_file).unwrap();
        tokio::time::sleep(Duration::from_millis(110)).await;
        assert_error(
            store
                .backend
                .create("three", Bytes::from_static(b"3"))
                .await,
            Error::Unavailable,
        );
        assert_eq!(fixture.calls.load(Ordering::SeqCst), 2);
        assert_eq!(fixture.tokens.load(Ordering::SeqCst), 4);
        server.abort();
    }
    #[test]
    fn rejects_credentials_endpoints_and_invalid_scope_in_configuration() {
        let good = config();
        good.build().unwrap(); // Lazy: no token read or network during configuration.
        for key in [
            "endpoint",
            "access_key",
            "sas_token",
            "client_secret",
            "authority_host",
        ] {
            let mut value = serde_json::to_value(&good).unwrap();
            value[key] = "forbidden".into();
            assert!(serde_json::from_value::<Config>(value).is_err());
        }
        for (field, value) in [
            ("account", "account.example.com"),
            ("container", "bad--container"),
            ("tenant_id", "invalid"),
            ("client_id", "00000000-0000-0000-0000-000000000000"),
            ("federated_token_file", "relative"),
            ("federated_token_file", "/tmp/../token"),
        ] {
            let mut config = serde_json::to_value(&good).unwrap();
            config[field] = value.into();
            assert!(serde_json::from_value::<Config>(config)
                .unwrap()
                .build()
                .is_err());
        }
    }
}
