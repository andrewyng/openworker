//! Native bounded HTTP transport. No redirects, environment proxy, mutation
//! replay, account-key fallback or raw credential/error output. Production hosts
//! are fixed; a loopback fixture constructor exists only inside unit tests.
use super::*;
use std::time::Duration;
use zeroize::Zeroizing;

pub(super) struct Reply {
    pub(super) status: u16,
    pub(super) etag: Option<String>,
    pub(super) operation: Option<String>,
    pub(super) body: Option<Value>,
}
pub(super) struct Azure {
    http: reqwest::Client,
    subscription: String,
    tenant: String,
    identity: Option<String>,
    #[cfg(test)]
    fixture_origin: Option<String>,
}
impl Azure {
    pub(super) fn new(p: &Plan, identity: Option<String>) -> Result<Self> {
        let Target::Azure {
            subscription,
            tenant,
            ..
        } = &p.target
        else {
            bail!("Azure target required")
        };
        Self::target(tenant, subscription, identity)
    }
    pub(super) fn target(
        tenant: &str,
        subscription: &str,
        identity: Option<String>,
    ) -> Result<Self> {
        ensure!(
            [tenant, subscription]
                .into_iter()
                .all(|s| uuid::Uuid::parse_str(s).is_ok_and(|u| !u.is_nil() && u.to_string() == s)),
            "Exact Azure tenant/subscription required"
        );
        ensure!(
            std::env::var_os("AZURE_CORE_DISABLE_CONNECTION_VERIFICATION").is_none(),
            "TLS override refused"
        );
        Ok(Self {
            http: reqwest::Client::builder()
                .no_proxy()
                .retry(reqwest::retry::never())
                .redirect(reqwest::redirect::Policy::none())
                .connect_timeout(Duration::from_secs(10))
                .timeout(Duration::from_secs(30))
                .build()?,
            subscription: subscription.to_owned(),
            tenant: tenant.to_owned(),
            identity,
            #[cfg(test)]
            fixture_origin: None,
        })
    }
    async fn token(&self, resource: &str) -> Result<Zeroizing<String>> {
        #[cfg(test)]
        if self.fixture_origin.is_some() {
            return Ok(Zeroizing::new("fixture-token".into()));
        }
        let v: Value = if let Some(client_id) = &self.identity {
            let endpoint = std::env::var("IDENTITY_ENDPOINT")
                .context("Container Apps identity endpoint unavailable")?;
            let url = reqwest::Url::parse(&endpoint)?;
            ensure!(
                url.scheme() == "http"
                    && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
                    && url.username().is_empty()
                    && url.password().is_none()
                    && url.query().is_none()
                    && url.fragment().is_none(),
                "Unexpected identity endpoint"
            );
            let header = Zeroizing::new(
                std::env::var("IDENTITY_HEADER")
                    .context("Container Apps identity header unavailable")?,
            );
            let response = self
                .http
                .get(url)
                .header("X-IDENTITY-HEADER", header.as_str())
                .query(&[
                    ("api-version", "2019-08-01"),
                    ("resource", resource),
                    ("client_id", client_id),
                ])
                .send()
                .await
                .context("Identity request failed")?;
            ensure!(response.status().is_success(), "Identity request rejected");
            bounded(response, 64 * 1024)
                .await?
                .context("Missing identity response")?
        } else {
            // Azure CLI only supplies a short-lived token in memory. Neither its
            // stderr nor response body may be copied into diagnostics/journals.
            let mut cmd = tokio::process::Command::new("az");
            cmd.args([
                "account",
                "get-access-token",
                "--subscription",
                &self.subscription,
                "--tenant",
                &self.tenant,
                "--resource",
                resource,
                "--only-show-errors",
                "-o",
                "json",
            ])
            .env("AZURE_EXTENSION_USE_DYNAMIC_INSTALL", "no")
            .env("AZURE_CORE_COLLECT_TELEMETRY", "false")
            .kill_on_drop(true);
            use tokio::io::AsyncReadExt;
            cmd.stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::null())
                .stdin(std::process::Stdio::null());
            let mut child = cmd.spawn().context("Token command unavailable")?;
            let stdout = child.stdout.take().unwrap();
            let mut bytes = Zeroizing::new(Vec::new());
            let mut bounded_stdout = stdout.take(65537);
            let operation = async {
                let (status, _) =
                    tokio::try_join!(child.wait(), bounded_stdout.read_to_end(&mut bytes))?;
                Ok::<_, std::io::Error>(status)
            };
            let status = tokio::time::timeout(Duration::from_secs(20), operation)
                .await
                .context("Token command timed out")?
                .context("Token command failed")?;
            ensure!(
                status.success() && bytes.len() <= 65536,
                "Token acquisition failed"
            );
            let v: Value = serde_json::from_slice(&bytes).context("Invalid credential response")?;
            bytes.clear();
            ensure!(
                v["tenant"].as_str() == Some(&self.tenant)
                    && v["subscription"].as_str() == Some(&self.subscription),
                "Token target mismatch"
            );
            v
        };
        let token = v
            .get("accessToken")
            .or_else(|| v.get("access_token"))
            .and_then(Value::as_str)
            .context("Missing access token")?;
        ensure!(
            !token.is_empty() && token.len() <= 32 * 1024 && !token.contains(['\r', '\n']),
            "Invalid access token"
        );
        Ok(Zeroizing::new(token.to_owned()))
    }
    pub(super) async fn arm(
        &mut self,
        method: reqwest::Method,
        id: &str,
        api: &str,
        body: Option<&Value>,
        request: &str,
    ) -> Result<Reply> {
        ensure!(
            id.starts_with(&format!(
                "/subscriptions/{}/resourceGroups/",
                self.subscription
            )) && !id.contains(['?', '#', '\\'])
                && !id.contains("..")
                && api.bytes().all(|b| b.is_ascii_digit() || b == b'-'),
            "ARM request outside resolved scope"
        );
        let token = self.token("https://management.azure.com/").await?;
        #[cfg(test)]
        let origin = self
            .fixture_origin
            .as_deref()
            .unwrap_or("https://management.azure.com");
        #[cfg(not(test))]
        let origin = "https://management.azure.com";
        let mut b = self
            .http
            .request(method, format!("{origin}{id}?api-version={api}"))
            .bearer_auth(token.as_str())
            .header("x-ms-client-request-id", request);
        if let Some(body) = body {
            b = b.json(body);
        }
        let r = b
            .send()
            .await
            .context("ARM outcome unknown; reconcile original request before retry")?;
        let status = r.status().as_u16();
        let etag = r
            .headers()
            .get("etag")
            .and_then(|h| h.to_str().ok())
            .map(str::to_owned);
        let operation = r
            .headers()
            .get("azure-asyncoperation")
            .or_else(|| r.headers().get("location"))
            .and_then(|h| h.to_str().ok())
            .map(str::to_owned);
        if let Some(u) = &operation {
            let u = reqwest::Url::parse(u)?;
            ensure!(
                u.scheme() == "https"
                    && u.host_str() == Some("management.azure.com")
                    && u.username().is_empty()
                    && u.password().is_none(),
                "Unexpected operation endpoint; reconcile resource instead"
            );
        }
        let body = bounded(r, 1024 * 1024).await?;
        Ok(Reply {
            status,
            etag,
            operation,
            body,
        })
    }
    pub(super) async fn blob(
        &mut self,
        method: reqwest::Method,
        url: &str,
        etag: Option<&str>,
        body: Option<&Value>,
    ) -> Result<Reply> {
        let u = reqwest::Url::parse(url)?;
        let host = u.host_str().context("Missing control host")?;
        let account = host
            .strip_suffix(".blob.core.windows.net")
            .context("Public Azure Blob endpoint required")?;
        ensure!(
            (3..=24).contains(&account.len())
                && account
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
                && u.scheme() == "https"
                && u.port().is_none()
                && u.username().is_empty()
                && u.password().is_none()
                && u.query().is_none()
                && u.fragment().is_none(),
            "Invalid control Blob URL"
        );
        let token = self.token("https://storage.azure.com/").await?;
        #[cfg(test)]
        let u = if let Some(origin) = &self.fixture_origin {
            reqwest::Url::parse(&format!("{origin}{}", u.path()))?
        } else {
            u
        };
        let mut b = self
            .http
            .request(method.clone(), u)
            .bearer_auth(token.as_str())
            .header("x-ms-version", "2023-11-03")
            .header(
                "x-ms-date",
                chrono::DateTime::from_timestamp(now()? as i64, 0)
                    .context("Invalid clock")?
                    .format("%a, %d %b %Y %H:%M:%S GMT")
                    .to_string(),
            );
        if method == reqwest::Method::PUT {
            b = b.header("x-ms-blob-type", "BlockBlob");
            if let Some(e) = etag {
                b = b.header("If-Match", e)
            } else {
                b = b.header("If-None-Match", "*")
            };
            b = b.json(body.context("Control body required")?);
        }
        let r = b
            .send()
            .await
            .context("Control outcome unknown; read original Blob before retry")?;
        let status = r.status().as_u16();
        let etag = r
            .headers()
            .get("etag")
            .and_then(|h| h.to_str().ok())
            .map(str::to_owned);
        // Blob PUT replies are XML/empty, while a successful GET is our JSON.
        let body = if method == reqwest::Method::GET && status == 200 {
            bounded(r, 1024 * 1024).await?
        } else {
            None
        };
        Ok(Reply {
            status,
            etag,
            operation: None,
            body,
        })
    }
}
async fn bounded(mut r: reqwest::Response, limit: usize) -> Result<Option<Value>> {
    let mut bytes = Vec::new();
    while let Some(b) = r
        .chunk()
        .await
        .context("Response body incomplete; outcome unknown")?
    {
        ensure!(
            bytes.len().saturating_add(b.len()) <= limit,
            "Provider response too large"
        );
        bytes.extend(b);
    }
    if bytes.is_empty() {
        return Ok(None);
    }
    serde_json::from_slice(&bytes)
        .map(Some)
        .context("Malformed provider response; outcome unknown")
}

#[cfg(test)]
mod tests {
    use super::*;
    use reqwest::Method;
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::{Arc, Mutex},
    };
    fn fixture(
        responses: Vec<Option<String>>,
    ) -> (Azure, Arc<Mutex<Vec<String>>>, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let log = Arc::new(Mutex::new(Vec::new()));
        let copy = log.clone();
        let task = std::thread::spawn(move || {
            for response in responses {
                let (mut conn, _) = listener.accept().unwrap();
                conn.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut bytes = Vec::new();
                let mut buf = [0u8; 4096];
                loop {
                    let n = conn.read(&mut buf).unwrap();
                    if n == 0 {
                        break;
                    }
                    bytes.extend_from_slice(&buf[..n]);
                    if let Some(end) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
                        let header = String::from_utf8_lossy(&bytes[..end]);
                        let len = header
                            .lines()
                            .find_map(|l| {
                                l.to_ascii_lowercase()
                                    .strip_prefix("content-length: ")
                                    .map(str::to_owned)
                            })
                            .and_then(|s| s.parse::<usize>().ok())
                            .unwrap_or(0);
                        if bytes.len() >= end + 4 + len {
                            break;
                        }
                    }
                }
                copy.lock().unwrap().push(String::from_utf8(bytes).unwrap());
                if let Some(response) = response {
                    conn.write_all(response.as_bytes()).unwrap();
                }
            }
        });
        let mut a = Azure::target(
            "11111111-2222-4333-8444-555555555555",
            "66666666-7777-4888-8999-aaaaaaaaaaaa",
            None,
        )
        .unwrap();
        a.fixture_origin = Some(format!("http://127.0.0.1:{port}"));
        (a, log, task)
    }
    fn response(status: &str, headers: &str, body: &str) -> String {
        format!(
            "HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: {}\r\n{headers}\r\n{body}",
            body.len()
        )
    }
    #[tokio::test]
    async fn control_uses_create_only_and_etag_cas_without_secret_headers() {
        let (mut a, log, t) = fixture(vec![
            Some(response("201 Created", "ETag: \"one\"\r\n", "")),
            Some(response("201 Created", "ETag: \"two\"\r\n", "")),
        ]);
        let url = "https://testaccount.blob.core.windows.net/control/ows-test.json";
        assert_eq!(
            a.blob(Method::PUT, url, None, Some(&json!({"safe":"metadata"})))
                .await
                .unwrap()
                .etag
                .as_deref(),
            Some("\"one\"")
        );
        a.blob(
            Method::PUT,
            url,
            Some("\"one\""),
            Some(&json!({"safe":"metadata"})),
        )
        .await
        .unwrap();
        t.join().unwrap();
        let requests = log.lock().unwrap();
        assert!(requests[0]
            .to_ascii_lowercase()
            .contains("if-none-match: *"));
        assert!(requests[1]
            .to_ascii_lowercase()
            .contains("if-match: \"one\""));
        assert!(!requests[0].contains("SharedKey"));
    }
    #[tokio::test]
    async fn lost_arm_write_has_exactly_one_transport_attempt() {
        let (mut a, log, t) = fixture(vec![None]);
        let r=a.arm(Method::PUT,"/subscriptions/66666666-7777-4888-8999-aaaaaaaaaaaa/resourceGroups/workload/providers/Microsoft.Resources/deployments/exact","2022-09-01",Some(&json!({})),"fixed-request").await;
        assert!(r.is_err());
        t.join().unwrap();
        let requests = log.lock().unwrap();
        assert_eq!(requests.len(), 1);
        assert!(requests[0]
            .to_ascii_lowercase()
            .contains("x-ms-client-request-id: fixed-request"));
    }
    #[tokio::test]
    async fn redirect_is_neither_followed_nor_accepted_as_operation() {
        let (mut a, log, t) = fixture(vec![Some(response(
            "307 Temporary Redirect",
            "Location: https://untrusted.invalid/steal\r\n",
            "",
        ))]);
        assert!(a.arm(Method::PUT,"/subscriptions/66666666-7777-4888-8999-aaaaaaaaaaaa/resourceGroups/workload/providers/Microsoft.Resources/deployments/exact","2022-09-01",Some(&json!({})),"fixed-request").await.is_err());
        t.join().unwrap();
        assert_eq!(log.lock().unwrap().len(), 1);
    }
}
