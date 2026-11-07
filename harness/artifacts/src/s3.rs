use crate::*;
use object_store::{
    aws::{AmazonS3, AmazonS3Builder, AwsCredentialProvider, S3ConditionalPut},
    ClientOptions, ObjectStore, ObjectStoreExt, PutMode, PutOptions, RetryConfig,
};
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub deployment: String,
    pub bucket: String,
    pub region: String,
    pub endpoint: Option<String>,
    /// Explicit trust anchor for a private HTTPS object-store endpoint.
    #[serde(default)]
    pub ca_file: Option<std::path::PathBuf>,
}
pub struct S3 {
    client: AmazonS3,
}
fn map(e: object_store::Error) -> anyhow::Error {
    match e {
        object_store::Error::NotFound { .. } => Error::NotFound.into(),
        object_store::Error::AlreadyExists { .. } | object_store::Error::Precondition { .. } => {
            Error::Conflict.into()
        }
        _ => Error::Unavailable.into(),
    }
}
impl Config {
    pub fn build(&self, credentials: Option<AwsCredentialProvider>) -> Result<Store> {
        // reqwest's no-provider feature deliberately requires an explicit choice.
        // Preserve an embedding application's already-installed crypto provider.
        if rustls::crypto::CryptoProvider::get_default().is_none() {
            let _ = rustls::crypto::ring::default_provider().install_default();
        }
        component(&self.deployment, 64)?;
        component(&self.region, 64)?;
        ensure!(
            (3..=63).contains(&self.bucket.len())
                && self
                    .bucket
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-'),
            Error::Invalid
        );
        let mut builder = AmazonS3Builder::new()
            .with_bucket_name(&self.bucket)
            .with_region(&self.region)
            .with_virtual_hosted_style_request(false)
            .with_conditional_put(S3ConditionalPut::ETagMatch)
            .with_retry(RetryConfig {
                max_retries: 0,
                retry_timeout: Duration::from_secs(15),
                ..Default::default()
            });
        let mut allow_http = false;
        let endpoint = if let Some(endpoint) = &self.endpoint {
            let url = url::Url::parse(endpoint).map_err(|_| Error::Invalid)?;
            ensure!(
                url.username().is_empty()
                    && url.password().is_none()
                    && url.query().is_none()
                    && url.fragment().is_none()
                    && url.path() == "/",
                Error::Invalid
            );
            let local = url.host_str().is_some_and(|h| {
                h == "localhost"
                    || h.trim_matches(['[', ']'])
                        .parse::<std::net::IpAddr>()
                        .is_ok_and(|ip| ip.is_loopback())
            });
            allow_http = url.scheme() == "http" && local;
            ensure!(url.scheme() == "https" || allow_http, Error::Invalid);
            let endpoint = url.to_string();
            builder = builder.with_endpoint(&endpoint);
            endpoint
        } else {
            format!("https://s3.{}.amazonaws.com/", self.region)
        };
        let mut options = ClientOptions::new()
            .with_allow_http(allow_http)
            .with_allow_invalid_certificates(false)
            .with_timeout(Duration::from_secs(15))
            .with_connect_timeout(Duration::from_secs(3));
        if let Some(path) = &self.ca_file {
            use std::io::Read;
            let mut pem = Vec::new();
            std::fs::File::open(path)
                .map_err(|_| Error::Invalid)?
                .take(65_537)
                .read_to_end(&mut pem)
                .map_err(|_| Error::Invalid)?;
            ensure!(pem.len() <= 65_536, Error::Invalid);
            let certificates =
                object_store::Certificate::from_pem_bundle(&pem).map_err(|_| Error::Invalid)?;
            ensure!(!certificates.is_empty(), Error::Invalid);
            for certificate in certificates {
                options = options.with_root_certificate(certificate);
            }
        }
        builder = builder.with_client_options(options);
        if let Some(credentials) = credentials {
            builder = builder.with_credentials(credentials);
        } else {
            // An explicitly configured Pod Identity source must be complete.
            // A missing token-file/endpoint must never fall through to the node's
            // instance role, which may have a different permission boundary.
            ensure!(
                std::env::var_os("AWS_CONTAINER_CREDENTIALS_FULL_URI").is_some()
                    == std::env::var_os("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE").is_some(),
                Error::Invalid
            );
            // Deliberately import credential inputs only, never ambient bucket,
            // endpoint, HTTP/TLS policy or arbitrary AWS configuration overrides.
            for key in [
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "AWS_WEB_IDENTITY_TOKEN_FILE",
                "AWS_ROLE_ARN",
                "AWS_ROLE_SESSION_NAME",
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                "AWS_CONTAINER_CREDENTIALS_FULL_URI",
                "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
            ] {
                if let Ok(value) = std::env::var(key) {
                    builder = builder.with_config(
                        key.to_ascii_lowercase()
                            .parse()
                            .map_err(|_| Error::Invalid)?,
                        value,
                    );
                }
            }
        }
        let identity = hash(
            format!(
                "s3-chunks-v1:{}:{}:{}:{}",
                self.deployment, self.bucket, self.region, endpoint
            )
            .as_bytes(),
        );
        Store::new(
            Arc::new(S3 {
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
impl Backend for S3 {
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
