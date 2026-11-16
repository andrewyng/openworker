//! Fixed Google API hosts, bounded bodies/deadlines, no redirects or HTTP retries.
use super::{control::*, engine::Cloud, *};
use reqwest::{Method, StatusCode};
use std::{process::Stdio, time::Duration};
use tokio::{io::AsyncReadExt, process::Command};
use zeroize::Zeroizing;
#[path = "deployment_gcp_installation_api.rs"]
mod installation;
#[cfg(test)]
#[path = "deployment_gcp_transport_tests.rs"]
mod transport_tests;

pub(super) struct Google {
    client: reqwest::Client,
    token: Zeroizing<String>,
    pub control: Control,
    deadline: std::time::Instant,
    #[cfg(test)]
    pub test_base: Option<String>,
}
fn client() -> Result<reqwest::Client> {
    Ok(reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(30))
        .build()?)
}
async fn body(mut r: reqwest::Response, max: usize) -> Result<Zeroizing<Vec<u8>>> {
    let mut out = Zeroizing::new(vec![]);
    while let Some(chunk) = r
        .chunk()
        .await
        .map_err(|_| anyhow::anyhow!("google_body_transport_error"))?
    {
        ensure!(out.len() + chunk.len() <= max, "google_response_limit");
        out.extend_from_slice(&chunk);
    }
    Ok(out)
}
impl Google {
    pub fn new(c: Control, token: Zeroizing<String>) -> Result<Self> {
        c.validate()?;
        ensure!(
            !token.is_empty() && token.len() <= 8192 && token.bytes().all(|b| b.is_ascii_graphic()),
            "Invalid ephemeral access token"
        );
        Ok(Self {
            client: client()?,
            token,
            control: c,
            deadline: std::time::Instant::now() + Duration::from_secs(150),
            #[cfg(test)]
            test_base: None,
        })
    }
    fn url(&self, host: &str, path: &str) -> Result<reqwest::Url> {
        ensure!(
            [
                "config.googleapis.com",
                "storage.googleapis.com",
                "run.googleapis.com",
                "cloudscheduler.googleapis.com",
                "compute.googleapis.com",
                "sqladmin.googleapis.com",
                "container.googleapis.com",
                "artifactregistry.googleapis.com",
                "cloudresourcemanager.googleapis.com",
                "serviceusage.googleapis.com"
            ]
            .contains(&host),
            "API host not allowed"
        );
        ensure!(
            path.starts_with('/') && !path.contains('?') && !path.contains('#'),
            "Invalid fixed API path"
        );
        #[cfg(test)]
        if let Some(base) = &self.test_base {
            return Ok(reqwest::Url::parse(&format!("{base}{path}"))?);
        }
        Ok(reqwest::Url::parse(&format!("https://{host}{path}"))?)
    }
    async fn request(
        &self,
        method: Method,
        host: &str,
        path: &str,
        query: &[(&str, String)],
        bytes: Option<Vec<u8>>,
        max: usize,
    ) -> Result<Option<Vec<u8>>> {
        let left = self
            .deadline
            .saturating_duration_since(std::time::Instant::now());
        ensure!(!left.is_zero(), "Google operation deadline exhausted");
        let mut request = self
            .client
            .request(method.clone(), self.url(host, path)?)
            .bearer_auth(&*self.token)
            .header("x-goog-user-project", project(&self.control.plan))
            .query(query)
            .timeout(left.min(Duration::from_secs(30)));
        if let Some(b) = bytes {
            request = request.header("Content-Type", "application/json").body(b);
        }
        let response = request
            .send()
            .await
            .map_err(|_| anyhow::anyhow!("google_transport_outcome_unknown"))?;
        if response.status() == StatusCode::NOT_FOUND && method == Method::GET {
            return Ok(None);
        }
        ensure!(
            response.status().is_success(),
            "google_http_status_{}",
            response.status().as_u16()
        );
        Ok(Some(body(response, max).await?.to_vec()))
    }
    pub(super) async fn json(
        &self,
        method: Method,
        host: &str,
        path: &str,
        query: &[(&str, String)],
        value: Option<Value>,
    ) -> Result<Option<Value>> {
        self.request(
            method,
            host,
            path,
            query,
            value.map(|v| serde_json::to_vec(&v)).transpose()?,
            1024 * 1024,
        )
        .await?
        .map(|b| serde_json::from_slice(&b).context("Invalid Google JSON response"))
        .transpose()
    }
    fn object_path(&self, name: &str) -> Result<String> {
        let mut url = self.url("storage.googleapis.com", "/storage/v1/b/")?;
        {
            let mut parts = url
                .path_segments_mut()
                .map_err(|_| anyhow::anyhow!("Invalid storage URL"))?;
            parts
                .pop_if_empty()
                .push(&self.control.bucket())
                .push("o")
                .push(name);
        }
        Ok(url.path().into())
    }
    pub async fn object(
        &self,
        name: &str,
        generation: Option<&str>,
        max: usize,
    ) -> Result<Option<(String, Vec<u8>)>> {
        let path = self.object_path(name)?;
        let q = generation
            .map(|g| vec![("generation", g.into())])
            .unwrap_or_default();
        let Some(meta) = self
            .json(Method::GET, "storage.googleapis.com", &path, &q, None)
            .await?
        else {
            return Ok(None);
        };
        let g = meta["generation"]
            .as_str()
            .context("Missing object generation")?;
        ensure!(
            meta["bucket"] == self.control.bucket()
                && meta["name"] == name
                && decimal(g)
                && generation.is_none_or(|expected| expected == g),
            "Object receipt scope mismatch"
        );
        ensure!(
            meta["size"]
                .as_str()
                .and_then(|s| s.parse::<usize>().ok())
                .is_some_and(|s| s <= max),
            "Invalid/oversized object"
        );
        let bytes = self
            .request(
                Method::GET,
                "storage.googleapis.com",
                &path,
                &[("generation", g.into()), ("alt", "media".into())],
                None,
                max,
            )
            .await?
            .context("Pinned object generation unavailable")?;
        Ok(Some((g.into(), bytes)))
    }
    pub async fn load(&self) -> Result<(Checkpoint, String)> {
        let (_, bytes) = self
            .object(&self.control.control_object()?, None, 1024 * 1024)
            .await?
            .context("Immutable control missing")?;
        ensure!(
            hash(&bytes) == self.control.sha()?,
            "Immutable control changed"
        );
        let (g, b) = self
            .object(&self.control.checkpoint_object(), None, 65536)
            .await?
            .context("Missing durable checkpoint")?;
        let j: Checkpoint = serde_json::from_slice(&b)?;
        j.validate(&self.control)?;
        Ok((j, g))
    }
    pub(super) async fn bucket(&self) -> Result<()> {
        let name = self.control.bucket();
        let v = self
            .json(
                Method::GET,
                "storage.googleapis.com",
                &format!("/storage/v1/b/{name}"),
                &[],
                None,
            )
            .await?
            .context("Control bucket missing")?;
        ensure!(
            v["name"] == name
                && v["projectNumber"]
                    == self
                        .control
                        .plan
                        .gcp_bootstrap
                        .as_ref()
                        .unwrap()
                        .project_number
                && v["iamConfiguration"]["uniformBucketLevelAccess"]["enabled"] == true
                && v["iamConfiguration"]["publicAccessPrevention"] == "enforced"
                && v["versioning"]["enabled"] == true,
            "Control bucket ownership/privacy drift"
        );
        Ok(())
    }
    async fn list(&self, path: &str, key: &str) -> Result<Vec<Value>> {
        let mut all = vec![];
        let mut token = String::new();
        let mut seen = std::collections::BTreeSet::new();
        for _ in 0..10 {
            let mut q = vec![("pageSize", "100".into())];
            if !token.is_empty() {
                q.push(("pageToken", token.clone()));
            }
            let v = self
                .json(Method::GET, "config.googleapis.com", path, &q, None)
                .await?
                .context("Provider list unavailable")?;
            ensure!(
                v["unreachable"].as_array().is_none_or(|a| a.is_empty()),
                "Incomplete provider inventory"
            );
            let entries = v
                .get(key)
                .map(|x| x.as_array().context("Invalid provider list"))
                .transpose()?
                .cloned()
                .unwrap_or_default();
            ensure!(
                all.len() + entries.len() <= 1000,
                "Provider inventory limit"
            );
            all.extend(entries);
            token = v
                .get("nextPageToken")
                .map(|x| x.as_str().context("Invalid pagination token"))
                .transpose()?
                .unwrap_or("")
                .into();
            if token.is_empty() {
                return Ok(all);
            }
            ensure!(
                token.len() <= 4096 && seen.insert(token.clone()),
                "Repeated/oversized pagination token"
            );
        }
        bail!("Provider inventory truncated")
    }
}

pub(super) async fn independent(uri: &str, sha: &str, expected_account: &str) -> Result<Google> {
    ensure!(digest(sha), "Invalid control digest");
    let source = reqwest::Url::parse(uri)?;
    ensure!(
        source.scheme() == "gs"
            && source.query().is_none()
            && source.fragment().is_none()
            && source.username().is_empty()
            && source.port().is_none(),
        "Use a plain control gs:// URI"
    );
    let bucket = source.host_str().context("Missing control bucket")?;
    ensure!(
        bucket.len() <= 63
            && bucket
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'),
        "Invalid control bucket"
    );
    let object = source
        .path()
        .strip_prefix('/')
        .context("Missing control object")?;
    ensure!(
        object.len() <= 200 && object.ends_with(&format!("/control-{sha}.json")),
        "Invalid content-addressed control object"
    );
    let token = metadata_token(expected_account).await?;
    let mut url = reqwest::Url::parse("https://storage.googleapis.com/storage/v1/b/")?;
    url.path_segments_mut()
        .unwrap()
        .pop_if_empty()
        .push(bucket)
        .push("o")
        .push(object);
    let response = client()?
        .get(url)
        .bearer_auth(&*token)
        .query(&[("alt", "media")])
        .send()
        .await
        .map_err(|_| anyhow::anyhow!("Control download failed"))?;
    ensure!(response.status().is_success(), "Control download failed");
    let bytes = body(response, 1024 * 1024).await?;
    ensure!(hash(&bytes) == sha, "Control digest mismatch");
    let c: Control = serde_json::from_slice(&bytes)?;
    c.validate()?;
    ensure!(
        c.sha()? == sha
            && c.control_uri()? == uri
            && accounts(&c.plan)["cleanup"] == expected_account,
        "Control scope/identity mismatch"
    );
    Google::new(c, token)
}
impl Cloud for Google {
    async fn deployment(&mut self) -> Result<Option<Value>> {
        self.json(
            Method::GET,
            "config.googleapis.com",
            &format!("/v1/{}", self.control.deployment()),
            &[],
            None,
        )
        .await
    }
    async fn operation(&mut self, name: &str) -> Result<Value> {
        self.control.operation(name)?;
        self.json(
            Method::GET,
            "config.googleapis.com",
            &format!("/v1/{name}"),
            &[],
            None,
        )
        .await?
        .context("Operation receipt unavailable")
    }
    async fn revision(&mut self, name: &str) -> Result<Value> {
        self.control.revision(name)?;
        self.json(
            Method::GET,
            "config.googleapis.com",
            &format!("/v1/{name}"),
            &[],
            None,
        )
        .await?
        .context("Revision receipt unavailable")
    }
    async fn operations(&mut self) -> Result<Vec<Value>> {
        self.list(
            &format!("/v1/{}/operations", self.control.parent()),
            "operations",
        )
        .await
    }
    async fn source(&mut self) -> Result<()> {
        self.bucket().await?;
        let (_, b) = self
            .object(
                &self.control.source_object(),
                Some(&self.control.source_generation),
                1024 * 1024,
            )
            .await?
            .context("Source missing")?;
        ensure!(
            hash(&b) == self.control.source_sha256,
            "Immutable source digest mismatch"
        );
        Ok(())
    }
    async fn guard(&mut self) -> Result<String> {
        let c = &self.control;
        let v = self
            .json(
                Method::GET,
                "run.googleapis.com",
                &format!("/v2/{}", c.job()),
                &[],
                None,
            )
            .await?
            .context("Independent cleanup job missing")?;
        let uid = v["uid"]
            .as_str()
            .filter(|s| uuid(s))
            .context("Missing cleanup job UID")?;
        let expected_args = super::control_export::tick_args(c)?;
        let execution = &v["template"];
        let task = &execution["template"];
        ensure!(
            v["name"] == c.job()
                && v.get("reconciling")
                    .is_none_or(|x| x.as_bool() == Some(false))
                && v["terminalCondition"]["state"] == "CONDITION_SUCCEEDED"
                && v["generation"].as_str().is_some_and(decimal)
                && v["generation"] == v["observedGeneration"],
            "Cleanup job not ready"
        );
        ensure!(
            execution["taskCount"] == 1
                && execution["parallelism"] == 1
                && task["serviceAccount"] == accounts(&c.plan)["cleanup"]
                && task.get("maxRetries").is_none_or(|x| x.as_u64() == Some(0))
                && task["timeout"] == "180s"
                && task["containers"].as_array().is_some_and(|a| a.len() == 1)
                && task["containers"][0]["image"] == c.guard_image
                && task["containers"][0]["command"] == json!(["/opt/openworker/ows"])
                && task["containers"][0]["args"] == json!(expected_args),
            "Cleanup runtime configuration drift"
        );
        let limits = &task["containers"][0]["resources"]["limits"];
        ensure!(
            ["1", "1000m"].iter().any(|s| limits["cpu"] == *s)
                && ["512Mi", "536870912"]
                    .iter()
                    .any(|s| limits["memory"] == *s),
            "Cleanup CPU/memory bounds changed"
        );
        ensure!(
            task.get("volumes")
                .is_none_or(|v| v.as_array().is_some_and(|a| a.is_empty()))
                && task["containers"][0]
                    .get("env")
                    .is_none_or(|v| v.as_array().is_some_and(|a| a.is_empty())),
            "Unexpected cleanup environment/volumes"
        );
        let schedule = self
            .json(
                Method::GET,
                "cloudscheduler.googleapis.com",
                &format!("/v1/{}", c.job()),
                &[],
                None,
            )
            .await?
            .context("Independent scheduler missing")?;
        ensure!(
            schedule["name"] == c.job()
                && schedule["state"] == "ENABLED"
                && schedule["schedule"] == "*/1 * * * *"
                && schedule["timeZone"] == "Etc/UTC"
                && schedule["attemptDeadline"] == "180s"
                && schedule["httpTarget"]["uri"]
                    == format!("https://run.googleapis.com/v2/{}:run", c.job())
                && schedule["httpTarget"]["httpMethod"] == "POST"
                && schedule["httpTarget"]["body"] == "e30="
                && schedule["httpTarget"]["headers"]["Content-Type"] == "application/json"
                && schedule["httpTarget"]["oauthToken"]["serviceAccountEmail"]
                    == accounts(&c.plan)["cleanup"]
                && schedule["httpTarget"]["oauthToken"]["scope"]
                    == "https://www.googleapis.com/auth/cloud-platform",
            "Cleanup scheduler drift"
        );
        Ok(uid.into())
    }
    async fn authorize_create(&mut self) -> Result<()> {
        bail!("GCP create remains blocked: full cost/effective-IAM/private-access and live guard qualification required")
    }
    async fn create(&mut self) -> Result<Value> {
        let c = &self.control;
        self.json(
            Method::POST,
            "config.googleapis.com",
            &format!("/v1/{}/deployments", c.parent()),
            &[
                ("deploymentId", c.plan.deployment_id.clone()),
                ("requestId", c.request_id.clone()),
            ],
            Some(c.request()),
        )
        .await?
        .context("Create response missing")
    }
    async fn delete(&mut self, request: &str) -> Result<Value> {
        ensure!(uuid(request), "Invalid delete request UUID");
        self.json(
            Method::DELETE,
            "config.googleapis.com",
            &format!("/v1/{}", self.control.deployment()),
            &[
                ("requestId", request.into()),
                ("force", "true".into()),
                ("deletePolicy", "DELETE".into()),
            ],
            None,
        )
        .await?
        .context("Delete response missing")
    }
    async fn save(&mut self, generation: &str, j: &Checkpoint) -> Result<String> {
        ensure!(decimal(generation), "Missing CAS generation");
        j.validate(&self.control)?;
        let path = format!("/upload/storage/v1/b/{}/o", self.control.bucket());
        let meta = self
            .json(
                Method::POST,
                "storage.googleapis.com",
                &path,
                &[
                    ("uploadType", "media".into()),
                    ("name", self.control.checkpoint_object()),
                    ("ifGenerationMatch", generation.into()),
                ],
                Some(serde_json::to_value(j)?),
            )
            .await?
            .context("Checkpoint CAS response missing")?;
        let g = meta["generation"]
            .as_str()
            .filter(|g| decimal(g))
            .context("Invalid checkpoint generation")?;
        ensure!(
            meta["bucket"] == self.control.bucket()
                && meta["name"] == self.control.checkpoint_object()
                && g != generation,
            "Checkpoint CAS receipt mismatch"
        );
        Ok(g.into())
    }
    async fn named_resources_absent(&mut self) -> Result<bool> {
        let c = &self.control;
        let p = project(&c.plan);
        let n = c.prefix();
        let z = &c.plan.gcp_infrastructure.as_ref().unwrap().zone;
        let targets = [
            (
                "container.googleapis.com",
                format!("/v1/projects/{p}/locations/{z}/clusters/{n}-gke"),
            ),
            (
                "sqladmin.googleapis.com",
                format!("/v1beta4/projects/{p}/instances/{n}-sql"),
            ),
            (
                "storage.googleapis.com",
                format!("/storage/v1/b/{p}-{n}-artifacts"),
            ),
            (
                "artifactregistry.googleapis.com",
                format!("/v1/{}/repositories/{n}-images", c.parent()),
            ),
            (
                "compute.googleapis.com",
                format!("/compute/v1/projects/{p}/global/networks/{n}-vpc"),
            ),
            (
                "compute.googleapis.com",
                format!(
                    "/compute/v1/projects/{p}/regions/{}/subnetworks/{n}-subnet",
                    c.region()
                ),
            ),
            (
                "compute.googleapis.com",
                format!(
                    "/compute/v1/projects/{p}/regions/{}/routers/{n}-router",
                    c.region()
                ),
            ),
            (
                "compute.googleapis.com",
                format!("/compute/v1/projects/{p}/global/addresses/{n}-sql-range"),
            ),
        ];
        let mut absent = true;
        for (host, path) in targets {
            if self
                .json(Method::GET, host, &path, &[], None)
                .await?
                .is_some()
            {
                absent = false;
            }
        }
        Ok(absent)
    }
    async fn dependent_assets(&mut self) -> Result<Vec<Asset>> {
        self.inventory().await
    }
    async fn assets_absent(&mut self, assets: &[Asset]) -> Result<bool> {
        let mut absent = true;
        for a in assets {
            a.validate(&self.control)?;
            if let Some(v) = self
                .json(Method::GET, "compute.googleapis.com", &a.path, &[], None)
                .await?
            {
                ensure!(
                    v["id"] == a.id,
                    "Captured dependent replaced; review instead of deleting/adopting it"
                );
                absent = false;
            }
        }
        Ok(absent)
    }
    async fn scheduler_paused(&mut self) -> Result<bool> {
        let c = &self.control;
        let v = self
            .json(
                Method::GET,
                "cloudscheduler.googleapis.com",
                &format!("/v1/{}", c.job()),
                &[],
                None,
            )
            .await?
            .context("Scheduler receipt missing")?;
        ensure!(
            v["name"] == c.job()
                && ["PAUSED", "ENABLED"].iter().any(|s| v["state"] == *s)
                && v["httpTarget"]["uri"]
                    == format!("https://run.googleapis.com/v2/{}:run", c.job())
                && v["httpTarget"]["oauthToken"]["serviceAccountEmail"]
                    == accounts(&c.plan)["cleanup"]
                && v["httpTarget"]["body"] == "e30=",
            "Scheduler scope changed"
        );
        Ok(v["state"] == "PAUSED")
    }
    async fn pause_scheduler(&mut self) -> Result<()> {
        self.json(
            Method::POST,
            "cloudscheduler.googleapis.com",
            &format!("/v1/{}:pause", self.control.job()),
            &[],
            Some(json!({})),
        )
        .await?
        .context("Pause receipt missing")?;
        Ok(())
    }
}

impl Google {
    async fn compute_list(&self, path: &str, filter: &str) -> Result<Vec<Value>> {
        let mut out = vec![];
        let mut token = String::new();
        let mut seen = std::collections::BTreeSet::new();
        for _ in 0..10 {
            let mut q = vec![("filter", filter.into()), ("maxResults", "100".into())];
            if !token.is_empty() {
                q.push(("pageToken", token.clone()));
            }
            let v = self
                .json(Method::GET, "compute.googleapis.com", path, &q, None)
                .await?
                .context("Dependent inventory unavailable")?;
            let items = v
                .get("items")
                .map(|a| a.as_array().context("Invalid dependent inventory"))
                .transpose()?
                .cloned()
                .unwrap_or_default();
            ensure!(out.len() + items.len() <= 1000, "Dependent inventory limit");
            out.extend(items);
            token = v
                .get("nextPageToken")
                .map(|v| v.as_str().context("Invalid dependent page token"))
                .transpose()?
                .unwrap_or("")
                .into();
            if token.is_empty() {
                return Ok(out);
            }
            ensure!(
                token.len() <= 4096 && seen.insert(token.clone()),
                "Dependent pagination loop"
            );
        }
        bail!("Dependent inventory truncated")
    }
    fn asset_path(&self, url: &str) -> Result<String> {
        let u = reqwest::Url::parse(url)?;
        ensure!(
            u.scheme() == "https"
                && [Some("www.googleapis.com"), Some("compute.googleapis.com")]
                    .contains(&u.host_str())
                && u.query().is_none()
                && u.fragment().is_none(),
            "Untrusted asset URI"
        );
        let a = Asset {
            path: u.path().into(),
            id: "1".into(),
        };
        a.validate(&self.control)?;
        Ok(a.path)
    }
    async fn collect(&self, url: &str, out: &mut BTreeMap<String, Asset>) -> Result<Option<Value>> {
        let path = self.asset_path(url)?;
        let Some(v) = self
            .json(Method::GET, "compute.googleapis.com", &path, &[], None)
            .await?
        else {
            return Ok(None);
        };
        let id = v["id"]
            .as_str()
            .filter(|id| decimal(id))
            .context("Missing dependent immutable ID")?;
        let a = Asset {
            path: path.clone(),
            id: id.into(),
        };
        a.validate(&self.control)?;
        if let Some(old) = out.insert(path, a) {
            ensure!(old.id == id, "Dependent replaced during inventory");
        }
        ensure!(out.len() <= 1000, "Dependent asset limit");
        Ok(Some(v))
    }
    async fn inventory(&self) -> Result<Vec<Asset>> {
        let c = &self.control;
        let p = project(&c.plan);
        let z = &c.plan.gcp_infrastructure.as_ref().unwrap().zone;
        let cluster = format!("{}-gke", c.prefix());
        let filter = format!("labels.goog-k8s-cluster-name = \"{cluster}\"");
        let mut assets = BTreeMap::new();
        let mut groups = std::collections::BTreeSet::new();
        if let Some(gke) = self
            .json(
                Method::GET,
                "container.googleapis.com",
                &format!("/v1/projects/{p}/locations/{z}/clusters/{cluster}"),
                &[],
                None,
            )
            .await?
        {
            let pools = gke
                .get("nodePools")
                .map(|v| v.as_array().context("Invalid GKE node-pool inventory"))
                .transpose()?
                .cloned()
                .unwrap_or_default();
            for pool in pools {
                let groups_for_pool = pool
                    .get("instanceGroupUrls")
                    .map(|v| v.as_array().context("Invalid GKE group inventory"))
                    .transpose()?
                    .cloned()
                    .unwrap_or_default();
                for group in groups_for_pool {
                    groups.insert(group.as_str().context("Invalid GKE group URI")?.to_owned());
                }
            }
        }
        for vm in self
            .compute_list(
                &format!("/compute/v1/projects/{p}/zones/{z}/instances"),
                &filter,
            )
            .await?
        {
            ensure!(
                vm["labels"]["goog-k8s-cluster-name"] == cluster,
                "Instance inventory scope mismatch"
            );
            let link = vm["selfLink"]
                .as_str()
                .context("Instance self-link missing")?;
            self.collect(link, &mut assets).await?;
            for disk in vm["disks"]
                .as_array()
                .context("Incomplete instance disk inventory")?
            {
                self.collect(
                    disk["source"].as_str().context("Disk URI missing")?,
                    &mut assets,
                )
                .await?;
            }
            if let Some(items) = vm["metadata"]["items"].as_array() {
                for item in items {
                    if item["key"] == "created-by" {
                        groups.insert(
                            item["value"]
                                .as_str()
                                .context("Invalid managed group URI")?
                                .into(),
                        );
                    }
                }
            }
        }
        for disk in self
            .compute_list(
                &format!("/compute/v1/projects/{p}/zones/{z}/disks"),
                &filter,
            )
            .await?
        {
            ensure!(
                disk["labels"]["goog-k8s-cluster-name"] == cluster,
                "Disk inventory scope mismatch"
            );
            self.collect(
                disk["selfLink"]
                    .as_str()
                    .context("Disk self-link missing")?,
                &mut assets,
            )
            .await?;
        }
        for group in groups {
            if let Some(v) = self.collect(&group, &mut assets).await? {
                if let Some(url) = v["instanceGroup"].as_str() {
                    self.collect(url, &mut assets).await?;
                }
                if let Some(url) = v["instanceTemplate"].as_str() {
                    self.collect(url, &mut assets).await?;
                }
                if let Some(versions) = v["versions"].as_array() {
                    for version in versions {
                        if let Some(url) = version["instanceTemplate"].as_str() {
                            self.collect(url, &mut assets).await?;
                        }
                    }
                }
            }
        }
        Ok(assets.into_values().collect())
    }
}

pub(super) async fn login(c: &Control, metadata: bool) -> Result<Zeroizing<String>> {
    if metadata {
        return metadata_token(&accounts(&c.plan)["cleanup"]).await;
    }
    for (key, value) in std::env::vars_os() {
        let key = key.to_string_lossy();
        let endpoint = key.starts_with("CLOUDSDK_API_ENDPOINT_OVERRIDES_");
        let impersonation = key == "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT" && !value.is_empty();
        ensure!(
            !(endpoint || impersonation),
            "Implicit cloud endpoint/identity override"
        );
    }
    let account = c.plan.gcp_bootstrap.as_ref().unwrap().email();
    let config = gcloud(c, account, &["config", "list", "--format=json"]).await?;
    let v: Value = serde_json::from_slice(config.as_bytes())?;
    ensure!(
        v.is_object()
            && ["api_endpoint_overrides", "auth", "core"]
                .iter()
                .all(|k| v.get(k).is_none_or(Value::is_object))
            && v["api_endpoint_overrides"]
                .as_object()
                .is_none_or(|m| m.is_empty())
            && [
                "impersonate_service_account",
                "credential_file_override",
                "access_token_file"
            ]
            .iter()
            .all(|k| v["auth"][k].as_str().is_none_or(str::is_empty))
            && v["core"]["universe_domain"]
                .as_str()
                .unwrap_or("googleapis.com")
                == "googleapis.com",
        "Untrusted gcloud configuration"
    );
    gcloud(
        c,
        account,
        &[
            "auth",
            "print-access-token",
            &format!(
                "--impersonate-service-account={}",
                accounts(&c.plan)["setup"]
            ),
        ],
    )
    .await
}
async fn gcloud(c: &Control, account: &str, args: &[&str]) -> Result<Zeroizing<String>> {
    let mut cmd = Command::new("gcloud");
    cmd.args(args)
        .args([
            "--project",
            project(&c.plan),
            "--billing-project",
            project(&c.plan),
            "--account",
            account,
            "--quiet",
            "--verbosity=error",
        ])
        .env("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")
        .env("CLOUDSDK_CORE_LOG_HTTP", "false")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    for key in [
        "CLOUDSDK_AUTH_ACCESS_TOKEN",
        "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE",
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ] {
        cmd.env_remove(key);
    }
    let mut child = cmd.spawn().context("gcloud unavailable")?;
    let out = child.stdout.take().unwrap();
    let err = child.stderr.take().unwrap();
    async fn capture(r: impl tokio::io::AsyncRead + Unpin) -> std::io::Result<Zeroizing<Vec<u8>>> {
        let mut b = Zeroizing::new(vec![]);
        r.take(16385).read_to_end(&mut b).await?;
        Ok(b)
    }
    let result = tokio::time::timeout(Duration::from_secs(30), async {
        tokio::try_join!(child.wait(), capture(out), capture(err))
    })
    .await;
    let (status, out, err) = match result {
        Ok(Ok(v)) => v,
        _ => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            bail!("gcloud authentication deadline/io failure")
        }
    };
    ensure!(
        status.success() && out.len() <= 16384 && err.len() <= 16384,
        "gcloud authentication failed"
    );
    Ok(Zeroizing::new(
        std::str::from_utf8(&out)
            .context("Invalid gcloud response")?
            .trim()
            .into(),
    ))
}

async fn metadata_token(expected: &str) -> Result<Zeroizing<String>> {
    ensure!(
        expected.ends_with(".iam.gserviceaccount.com")
            && expected.len() <= 254
            && expected
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"@._-".contains(&b)),
        "Invalid cleanup service account"
    );
    // Explicit Cloud Run mode only. Never consume caller-supplied metadata URLs.
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .timeout(Duration::from_secs(5))
        .build()?;
    let base =
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default";
    let email = client
        .get(format!("{base}/email"))
        .header("Metadata-Flavor", "Google")
        .send()
        .await
        .map_err(|_| anyhow::anyhow!("Metadata identity unavailable"))?;
    ensure!(
        email.status().is_success()
            && email
                .headers()
                .get("Metadata-Flavor")
                .is_some_and(|v| v == "Google"),
        "Untrusted metadata response"
    );
    ensure!(
        body(email, 512).await?.as_slice() == expected.as_bytes(),
        "Cleanup metadata identity mismatch"
    );
    let r = client
        .get(format!("{base}/token"))
        .header("Metadata-Flavor", "Google")
        .send()
        .await
        .map_err(|_| anyhow::anyhow!("Metadata token unavailable"))?;
    ensure!(
        r.status().is_success()
            && r.headers()
                .get("Metadata-Flavor")
                .is_some_and(|v| v == "Google"),
        "Untrusted metadata token response"
    );
    let bytes = body(r, 16384).await?;
    let v: Value = serde_json::from_slice(&bytes)?;
    ensure!(
        v["token_type"] == "Bearer" && v["expires_in"].as_u64().is_some_and(|s| s >= 300),
        "Invalid metadata token lifetime"
    );
    Ok(Zeroizing::new(
        v["access_token"]
            .as_str()
            .context("Missing metadata token")?
            .into(),
    ))
}
