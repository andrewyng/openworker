//! Publish one reviewed Linux ARM64 image to an owned immutable ECR tag.
//! A lost push reply is reconciled by tag+manifest+config digest, never blindly retried.
use super::*;
struct PrivateDirectory(PathBuf);
impl Drop for PrivateDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
async fn tool(program: &str, args: Vec<String>, input: Option<Vec<u8>>) -> Result<Vec<u8>> {
    local::exec(Path::new(program), &args, input).await
}
fn strings(args: &[&str]) -> Vec<String> {
    args.iter().map(|s| s.to_string()).collect()
}
async fn observed(
    api: &Aws,
    repository: &str,
    tag: &str,
    config_digest: &str,
) -> Result<Option<String>> {
    let Target::Aws { account, .. } = &api.p.target else {
        unreachable!()
    };
    let v=api.json("ecr","batch-get-image",json!({"registryId":account,"repositoryName":repository,"imageIds":[{"imageTag":tag}],"acceptedMediaTypes":["application/vnd.docker.distribution.manifest.v2+json","application/vnd.oci.image.manifest.v1+json"]})).await?;
    let images = v["images"]
        .as_array()
        .context("Invalid ECR image inventory")?;
    let failures = v["failures"]
        .as_array()
        .context("Invalid ECR failure inventory")?;
    if images.is_empty() {
        ensure!(
            failures.len() == 1
                && failures[0]["failureCode"] == "ImageNotFound"
                && failures[0]["imageId"]["imageTag"] == tag,
            "ECR failure is not proof of absence"
        );
        return Ok(None);
    }
    ensure!(
        images.len() == 1 && failures.is_empty(),
        "Ambiguous ECR image result"
    );
    let i = &images[0];
    ensure!(
        i["registryId"] == *account
            && i["repositoryName"] == repository
            && i["imageId"]["imageTag"] == tag,
        "ECR image belongs to another scope"
    );
    let text = i["imageManifest"]
        .as_str()
        .context("ECR manifest missing")?;
    let manifest: Value = serde_json::from_str(text).context("Invalid ECR manifest")?;
    ensure!(
        manifest["schemaVersion"] == 2 && manifest["config"]["digest"] == config_digest,
        "Pushed image does not match original local configuration"
    );
    let digest = format!("sha256:{}", hash(text.as_bytes()));
    ensure!(
        i["imageId"]["imageDigest"] == digest,
        "Remote manifest digest mismatch"
    );
    Ok(Some(digest))
}
pub(super) async fn publish(
    root: &Path,
    profile: &str,
    role: &str,
    source: &str,
    allow: bool,
    confirm: bool,
) -> Result<()> {
    ensure!(
        allow,
        "Explicit --allow-experimental-cloud required; no cloud calls made"
    );
    ensure!(
        ["harness", "runner", "service-ui"].contains(&role),
        "Unknown deployment image role"
    );
    ensure!(
        !source.is_empty()
            && source.len() <= 256
            && source.as_bytes()[0].is_ascii_alphanumeric()
            && source
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"/.:_@-".contains(&b)),
        "Explicit trusted local image reference required"
    );
    let binding = install::Binding {
        state: root.canonicalize()?,
        profile: profile.into(),
    };
    let (mut j, p, api, _cluster, outputs, _lock) = install::snapshot(&binding).await?;
    ensure!(
        now()? < p.expires_at_unix,
        "Expiry prevents new image publication"
    );
    // Remove only this installer's private remnants left by a killed prior publisher.
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        if entry
            .file_name()
            .to_string_lossy()
            .starts_with(".aws-publish-")
        {
            let meta = fs::symlink_metadata(entry.path())?;
            ensure!(
                meta.is_dir() && !meta.file_type().is_symlink(),
                "Invalid private publisher directory"
            );
            fs::remove_dir_all(entry.path())?;
        }
    }
    let temp = root.join(format!(".aws-publish-{}", uuid::Uuid::new_v4().simple()));
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(&temp)?;
    let _private = PrivateDirectory(temp.clone());
    let host = String::from_utf8(
        tool(
            "docker",
            strings(&[
                "context",
                "inspect",
                "--format",
                "{{.Endpoints.docker.Host}}",
            ]),
            None,
        )
        .await?,
    )?;
    let host = host.trim();
    ensure!(
        host.starts_with("unix://") && !host.chars().any(char::is_whitespace),
        "Publisher requires an explicit local Unix Docker socket"
    );
    let docker = |args: Vec<String>| {
        let mut all = strings(&["--config", temp.to_str().unwrap(), "--host", host]);
        all.extend(args);
        all
    };
    let meta: Value = serde_json::from_slice(
        &tool(
            "docker",
            docker(strings(&["image", "inspect", source])),
            None,
        )
        .await?,
    )?;
    ensure!(
        meta.as_array().is_some_and(|a| a.len() == 1)
            && meta[0]["Os"] == "linux"
            && meta[0]["Architecture"] == "arm64"
            && meta[0]["Size"]
                .as_u64()
                .is_some_and(|n| n <= 2 * 1024 * 1024 * 1024),
        "Publisher accepts bounded Linux ARM64 images only"
    );
    let local_id = meta[0]["Id"]
        .as_str()
        .context("Local image identity missing")?
        .to_string();
    ensure!(
        local_id
            .strip_prefix("sha256:")
            .is_some_and(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())),
        "Invalid Docker content identity"
    );
    let archive = temp.join("image.tar");
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    drop(options.open(&archive)?);
    tool(
        "docker",
        docker(strings(&[
            "image",
            "save",
            "--platform",
            "linux/arm64",
            "--output",
            archive.to_str().unwrap(),
            &local_id,
        ])),
        None,
    )
    .await?;
    let manifests: Value = serde_json::from_slice(
        &tool(
            "tar",
            strings(&["-xOf", archive.to_str().unwrap(), "manifest.json"]),
            None,
        )
        .await?,
    )?;
    ensure!(
        manifests.as_array().is_some_and(|a| a.len() == 1),
        "Ambiguous local image archive"
    );
    let config = manifests[0]["Config"]
        .as_str()
        .context("Local configuration identity missing")?;
    let identifier = config
        .strip_prefix("blobs/sha256/")
        .or_else(|| config.strip_suffix(".json"))
        .context("Unsupported local config path")?;
    ensure!(
        identifier.len() == 64
            && identifier
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "Invalid local config path"
    );
    let config_bytes = tool(
        "tar",
        strings(&["-xOf", archive.to_str().unwrap(), config]),
        None,
    )
    .await?;
    ensure!(
        hash(&config_bytes) == identifier,
        "Local config archive digest mismatch"
    );
    let content: Value =
        serde_json::from_slice(&config_bytes).context("Invalid local image configuration")?;
    ensure!(
        content["os"] == "linux" && content["architecture"] == "arm64",
        "Local archive platform mismatch"
    );
    let config_digest = format!("sha256:{identifier}");
    let Target::Aws { account, region } = &p.target else {
        unreachable!()
    };
    let repository = format!("{}/{role}", p.prefix());
    let uri = format!("{account}.dkr.ecr.{region}.amazonaws.com/{repository}");
    let key = format!("publish:{role}");
    let output_key = match role {
        "harness" => "HarnessRepository",
        "runner" => "RunnerRepository",
        _ => "UiRepository",
    };
    ensure!(
        outputs.get(output_key) == Some(&uri),
        "Image repository differs from original stack"
    );
    let repo = api
        .json(
            "ecr",
            "describe-repositories",
            json!({"registryId":account,"repositoryNames":[repository]}),
        )
        .await?;
    let rows = repo["repositories"]
        .as_array()
        .context("Repository metadata missing")?;
    ensure!(
        rows.len() == 1
            && rows[0]["repositoryUri"] == uri
            && rows[0]["registryId"] == *account
            && rows[0]["imageTagMutability"] == "IMMUTABLE"
            && rows[0]["repositoryArn"]
                == format!("arn:aws:ecr:{region}:{account}:repository/{repository}")
            && rows[0].get("createdAt").is_some_and(|v| !v.is_null()),
        "Repository identity/mutability changed"
    );
    if let Some(previous) = j.resources.get(&key) {
        ensure!(
            previous["localId"] == local_id
                && previous["configDigest"] == config_digest
                && previous["repositoryCreatedAt"] == rows[0]["createdAt"],
            "Image/repository identity changed since original publish intent"
        );
    }
    let mut first = !j.operations.contains_key(&key);
    if confirm {
        let previous = j
            .operations
            .get(&key)
            .context("No uncertain publish intent")?;
        ensure!(
            previous.status == "intent" && j.recovery_history.len() < 16,
            "Only an unconfirmed publish can be reset"
        );
        let tag = format!("ows-{}", previous.token);
        ensure!(
            observed(&api, &repository, &tag, &config_digest)
                .await?
                .is_none(),
            "Original image exists; reconcile it instead"
        );
        let old = j.operations.remove(&key).unwrap();
        j.recovery_history.push((key.clone(), old));
        j.resources.remove(&key);
        first = true;
    }
    if first {
        j.operations.insert(
            key.clone(),
            state::Operation {
                token: uuid::Uuid::new_v4().simple().to_string(),
                arn: Some(
                    rows[0]["repositoryArn"]
                        .as_str()
                        .context("Repository ARN missing")?
                        .into(),
                ),
                status: "prepared".into(),
            },
        );
        j.resources.insert(key.clone(),json!({"localId":local_id,"configDigest":config_digest,"repositoryCreatedAt":rows[0]["createdAt"]}));
        state::save(root, &j)?;
    }
    let tag = format!("ows-{}", j.operations[&key].token);
    let mut remote = observed(&api, &repository, &tag, &config_digest).await?;
    if remote.is_none() {
        ensure!(j.operations[&key].status=="prepared","Image push outcome unknown; inspect original immutable tag before explicit --confirm-no-effect");
        let auth = api
            .json(
                "ecr",
                "get-authorization-token",
                json!({"registryIds":[account]}),
            )
            .await?;
        let rows = auth["authorizationData"]
            .as_array()
            .context("Registry authentication missing")?;
        let registry = format!("{account}.dkr.ecr.{region}.amazonaws.com");
        ensure!(
            rows.len() == 1 && rows[0]["proxyEndpoint"] == format!("https://{registry}"),
            "Registry authentication scope mismatch"
        );
        let token = Zeroizing::new(
            base64::engine::general_purpose::STANDARD
                .decode(
                    rows[0]["authorizationToken"]
                        .as_str()
                        .context("Registry token missing")?,
                )
                .context("Invalid registry token")?,
        );
        let token = std::str::from_utf8(&token).context("Invalid registry token")?;
        let password = token
            .strip_prefix("AWS:")
            .context("Invalid registry user")?;
        tool(
            "docker",
            docker(strings(&[
                "login",
                "--username",
                "AWS",
                "--password-stdin",
                &registry,
            ])),
            Some(password.as_bytes().to_vec()),
        )
        .await?;
        let target = format!("{uri}:{tag}");
        tool(
            "docker",
            docker(strings(&["image", "tag", &local_id, &target])),
            None,
        )
        .await?;
        j.operations.get_mut(&key).unwrap().status = "intent".into();
        state::save(root, &j)?;
        tool(
            "docker",
            docker(strings(&[
                "image",
                "push",
                "--platform",
                "linux/arm64",
                &target,
            ])),
            None,
        )
        .await?;
        remote = observed(&api, &repository, &tag, &config_digest).await?;
    }
    let digest =
        remote.context("Push not observed; inspect original immutable tag, no automatic retry")?;
    let result = format!("{uri}@{digest}");
    j.resources.get_mut(&key).unwrap()["image"] = json!(result);
    j.operations.get_mut(&key).unwrap().status = "complete".into();
    state::save(root, &j)?;
    crate::output(
        json!({"role":role,"image":result,"configDigest":config_digest,"credentialsPersistedInJournal":false,"platform":"linux/arm64"}),
    )
}
