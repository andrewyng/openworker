//! Private, narrowly scoped desired-state reconciler. No Kubernetes proxy is exposed over HTTP.
use anyhow::{ensure, Context, Result};
use openworker_store_postgres::{
    service_pools::{Binding, Ownership},
    PostgresStore,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::PathBuf;
#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Config {
    pub endpoint: String,
    pub ca_file: PathBuf,
    pub token_file: PathBuf,
    pub pools: Vec<Target>,
}
#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Target {
    pub id: String,
    pub namespace: String,
    pub deployment: String,
    pub ownership: Ownership,
    pub max_replicas: i32,
}
fn name(s: &str) -> Result<()> {
    ensure!(
        !s.is_empty()
            && s.len() <= 63
            && s.bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'),
        "Invalid Kubernetes target name"
    );
    Ok(())
}
// Kubernetes owns these projected paths and rotates their symlink targets.
// This narrowly scoped reader is not used for user-supplied coworker/tool files.
fn projected(path: &std::path::Path, limit: usize) -> Result<Vec<u8>> {
    use std::io::Read;
    let mut data = Vec::new();
    std::fs::File::open(path)?
        .take(limit as u64 + 1)
        .read_to_end(&mut data)?;
    ensure!(data.len() <= limit, "Projected credential too large");
    Ok(data)
}
struct Client {
    http: reqwest::Client,
    endpoint: String,
    token_file: PathBuf,
}
impl Client {
    fn new(c: &Config) -> Result<Self> {
        let url = reqwest::Url::parse(&c.endpoint)?;
        ensure!(
            url.scheme() == "https"
                && url.username().is_empty()
                && url.password().is_none()
                && url.query().is_none()
                && url.fragment().is_none()
                && url.path() == "/",
            "Invalid Kubernetes endpoint"
        );
        ensure!(
            !c.pools.is_empty() && c.pools.len() <= 16,
            "Invalid pool count"
        );
        let ca = projected(&c.ca_file, 256000)?;
        let http = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(std::time::Duration::from_secs(10))
            .add_root_certificate(reqwest::Certificate::from_pem(&ca)?)
            .build()?;
        Ok(Self {
            http,
            endpoint: c.endpoint.trim_end_matches('/').into(),
            token_file: c.token_file.clone(),
        })
    }
    async fn request(&self, path: &str, patch: Option<Value>) -> Result<Value> {
        // Projected service-account tokens rotate; read at every request, never cache or log.
        let token =
            zeroize::Zeroizing::new(String::from_utf8(projected(&self.token_file, 16000)?)?);
        let mut q = if patch.is_some() {
            self.http.patch(format!("{}{path}", self.endpoint))
        } else {
            self.http.get(format!("{}{path}", self.endpoint))
        };
        q = q.bearer_auth(token.trim());
        if let Some(p) = patch {
            q = q
                .header("content-type", "application/merge-patch+json")
                .json(&p);
        }
        let mut r = q
            .send()
            .await
            .map_err(|_| anyhow::anyhow!("Kubernetes connection failed"))?;
        ensure!(r.status().is_success(), "Kubernetes request rejected");
        let mut body = Vec::new();
        while let Some(chunk) = r.chunk().await? {
            ensure!(
                body.len() + chunk.len() <= 2_000_000,
                "Kubernetes response too large"
            );
            body.extend(chunk);
        }
        serde_json::from_slice(&body).context("Invalid Kubernetes response")
    }
}
fn replicas(scale: &Value) -> Result<i64> {
    let spec = scale["spec"].as_object().context("Missing scale spec")?;
    // Kubernetes ScaleSpec omits replicas when its integer value is zero.
    let n = match spec.get("replicas") {
        None => 0,
        Some(v) => v.as_i64().context("Invalid scale count")?,
    };
    ensure!(n >= 0, "Invalid negative scale count");
    Ok(n)
}
pub async fn reconcile(c: &Config, store: &PostgresStore, service_id: &str) -> Result<()> {
    let client = Client::new(c)?;
    for target in &c.pools {
        for s in [&target.id, &target.namespace, &target.deployment] {
            name(s)?;
        }
        let path = format!(
            "/apis/apps/v1/namespaces/{}/deployments/{}",
            target.namespace, target.deployment
        );
        let deployment = client.request(&path, None).await?;
        ensure!(
            deployment["metadata"]["labels"]["openworker.io/service-id"] == service_id
                && deployment["metadata"]["labels"]["openworker.io/pool-id"] == target.id,
            "Kubernetes target ownership mismatch"
        );
        let uid = deployment["metadata"]["uid"]
            .as_str()
            .context("Missing deployment UID")?;
        let initial = deployment["spec"]["replicas"]
            .as_i64()
            .context("Missing replica count")? as i32;
        let binding = Binding {
            id: target.id.clone(),
            namespace: target.namespace.clone(),
            deployment: target.deployment.clone(),
            uid: uid.into(),
            ownership: target.ownership.clone(),
            max_replicas: target.max_replicas,
        };
        // Restart never adopts a same-name replacement or overwrites the last desired count.
        let pool = store.service_bind_pool(binding, initial)?;
        let result:Result<Value>=async {
            let scale_path=format!("{path}/scale");
            let current=client.request(&scale_path,None).await?;
            ensure!(current["metadata"]["uid"]==uid,"Scale target UID changed");
            let actual=replicas(&current)?;
            if pool.binding.ownership==Ownership::Manual && actual!=pool.desired_replicas as i64 {
                let version=current["metadata"]["resourceVersion"].as_str().context("Missing resource version")?;
                // Setting desired capacity is idempotent. A conflict/connection loss is
                // resolved by re-reading; no blind patch retry and no tool-action replay.
                client.request(&scale_path,Some(json!({"metadata":{"resourceVersion":version,"uid":uid},"spec":{"replicas":pool.desired_replicas}}))).await?;
            }
            let pods=client.request(&format!("/api/v1/namespaces/{}/pods?labelSelector=openworker.io%2Fpool-id%3D{}%2Copenworker.io%2Fservice-id%3D{}",target.namespace,target.id,service_id),None).await?;
            let pods=pods["items"].as_array().context("Missing pod list")?;
            let active=pods.iter().filter(|p|!matches!(p["status"]["phase"].as_str(),Some("Succeeded"|"Failed"))).count();
            let draining=pods.iter().filter(|p|!p["metadata"]["deletionTimestamp"].is_null()).count();
            let ready=deployment["status"]["readyReplicas"].as_i64().unwrap_or(0);
            let target_count=if pool.binding.ownership==Ownership::Manual {pool.desired_replicas as i64}else{actual};
            let status=if draining>0 {"draining"}else if actual==target_count&&active as i64==target_count&&ready==target_count {"applied"}else{"pending"};
            Ok(json!({"status":status,"observedRevision":pool.revision,"replicas":actual,"readyReplicas":ready,"activePods":active,"drainingPods":draining,"deploymentUid":uid}))
        }.await;
        store.service_observe_pool(&target.id,pool.revision,result.unwrap_or_else(|_|json!({"status":"blocked","observedRevision":pool.revision,"reason":"Kubernetes reconciliation failed; inspect controller connectivity and RBAC"})))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn kubernetes_zero_scale_omits_desired_replica_field() {
        assert_eq!(
            replicas(&json!({"kind":"Scale","spec":{},"status":{"replicas":0}})).unwrap(),
            0
        );
        assert_eq!(replicas(&json!({"spec":{"replicas":2}})).unwrap(), 2);
        assert!(replicas(&json!({"spec":{"replicas":"two"}})).is_err());
        assert!(replicas(&json!({"spec":{"replicas":-1}})).is_err());
        assert!(replicas(&json!({})).is_err());
    }
}
