//! Opt-in AWS qualification only. Run inside a disposable EKS pod, with an
//! application-supplied non-secret spec. Never prints credential/token contents.
use anyhow::{ensure, Context, Result};
use object_store::{
    aws::{AmazonS3, AmazonS3Builder, AwsAuthorizer},
    client::{HttpConnector, HttpRequest, HttpRequestBody, ReqwestConnector},
    path::Path,
    ClientOptions, ObjectStore, ObjectStoreExt, PutMode, PutOptions, RetryConfig,
};
use openworker_artifacts::{component, s3::Config};
use serde::Deserialize;
use serde_json::json;
use std::time::Duration;
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Spec {
    deployment: String,
    bucket: String,
    region: String,
    foreign_bucket: String,
    own_key: String,
    expected_role_prefix: String,
    nonce: String,
}
fn client(spec: &Spec, bucket: &str) -> Result<AmazonS3> {
    Ok(AmazonS3Builder::new()
        .with_bucket_name(bucket)
        .with_region(&spec.region)
        .with_config(
            "aws_container_credentials_full_uri".parse()?,
            std::env::var("AWS_CONTAINER_CREDENTIALS_FULL_URI")?,
        )
        .with_config(
            "aws_container_authorization_token_file".parse()?,
            std::env::var("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE")?,
        )
        .with_client_options(
            ClientOptions::new()
                .with_timeout(Duration::from_secs(15))
                .with_connect_timeout(Duration::from_secs(3)),
        )
        .with_retry(RetryConfig {
            max_retries: 0,
            ..Default::default()
        })
        .build()?)
}
async fn denied(store: &AmazonS3, path: &Path) -> Result<()> {
    ensure!(
        matches!(
            store.get(path).await,
            Err(object_store::Error::PermissionDenied { .. })
        ),
        "Expected actual AccessDenied on foreign read"
    );
    // Conditional create protects any existing key even if an IAM policy regresses.
    // The operator records this exact disposable test key before running the probe.
    let put = store
        .put_opts(
            path,
            vec![1_u8].into(),
            PutOptions {
                mode: PutMode::Create,
                ..Default::default()
            },
        )
        .await;
    ensure!(
        matches!(put, Err(object_store::Error::PermissionDenied { .. })),
        "Expected actual AccessDenied on foreign write"
    );
    Ok(())
}
async fn run() -> Result<()> {
    for key in [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    ] {
        ensure!(
            std::env::var_os(key).is_none(),
            "Competing credential source must be absent"
        );
    }
    let spec: Spec = serde_json::from_str(&std::env::var("OW_ARTIFACT_PROBE_SPEC")?)?;
    component(&spec.deployment, 64)?;
    component(&spec.nonce, 64)?;
    ensure!(
        spec.own_key.starts_with(&format!("{}/", spec.deployment)),
        "Own key must bind deployment"
    );
    ensure!(
        spec.foreign_bucket != spec.bucket,
        "Foreign bucket must differ"
    );
    // Also builds the production adapter with its normal environment chain.
    Config {
        deployment: spec.deployment.clone(),
        bucket: spec.bucket.clone(),
        region: spec.region.clone(),
        endpoint: None,
        ca_file: None,
    }
    .build(None)?;
    let store = client(&spec, &spec.bucket)?;
    let credential = store.credentials().get_credential().await?;
    let mut request = HttpRequest::new(HttpRequestBody::empty());
    *request.uri_mut() = format!(
        "https://sts.{}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        spec.region
    )
    .parse()?;
    AwsAuthorizer::new(&credential, "sts", &spec.region).try_authorize(&mut request, None)?;
    let response = ReqwestConnector::default()
        .connect(&ClientOptions::new().with_timeout(Duration::from_secs(15)))?
        .execute(request)
        .await?;
    ensure!(
        response.status().is_success(),
        "STS identity request failed"
    );
    let body = response.into_body().bytes().await?;
    let xml = std::str::from_utf8(&body)?;
    let arn = xml
        .split_once("<Arn>")
        .and_then(|(_, s)| s.split_once("</Arn>").map(|(arn, _)| arn))
        .context("STS identity missing")?;
    ensure!(
        arn.starts_with(&spec.expected_role_prefix),
        "Unexpected workload identity"
    );
    store
        .get(&Path::parse(&spec.own_key)?)
        .await?
        .bytes()
        .await?;
    let foreign_path = Path::parse(format!("foreign-{}/probe-{}", spec.deployment, spec.nonce))?;
    denied(&store, &foreign_path).await?;
    denied(&client(&spec, &spec.foreign_bucket)?, &foreign_path).await?;
    println!(
        "{}",
        json!({"passed":true,"identityArn":arn,"podIdentityOnly":true,"ownPrefixReadable":true,
        "foreignPrefixReadDenied":true,"foreignPrefixWriteDenied":true,"foreignBucketReadDenied":true,"foreignBucketWriteDenied":true,
        "denialRequiresAccessDenied":true,"automaticMutationRetries":false})
    );
    Ok(())
}
#[tokio::main]
async fn main() {
    if run().await.is_err() {
        eprintln!("Artifact identity/access probe failed; credential and response contents withheld. Inspect the original disposable probe and storage receipts before retrying.");
        std::process::exit(1);
    }
}
