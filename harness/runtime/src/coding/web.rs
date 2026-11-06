use anyhow::{bail, ensure, Context, Result};
use serde_json::{json, Value};
use std::{
    net::{IpAddr, SocketAddr},
    time::Duration,
};
fn public(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v) => {
            let n = u32::from(v);
            ![
                (0u32, 8),
                (0x0a000000, 8),
                (0x64400000, 10),
                (0x7f000000, 8),
                (0xa9fe0000, 16),
                (0xac100000, 12),
                (0xc0000000, 24),
                (0xc0000200, 24),
                (0xc0586300, 24),
                (0xc0a80000, 16),
                (0xc6120000, 15),
                (0xc6336400, 24),
                (0xcb007100, 24),
                (0xe0000000, 3),
            ]
            .iter()
            .any(|(base, bits)| n >> (32 - bits) == base >> (32 - bits))
        }
        IpAddr::V6(v) => {
            let b = v.octets();
            b[0] & 0xe0 == 0x20
                && !(b[..2] == [0x20, 0x01] && b[2] < 2)
                && b[..2] != [0x20, 0x02]
                && b[..4] != [0x20, 0x01, 0x0d, 0xb8]
                && !(b[..2] == [0x3f, 0xff] && b[2] < 16)
        }
    }
}
fn url(raw: &str) -> Result<reqwest::Url> {
    ensure!(raw.len() <= 4096, "URL too long");
    let u = reqwest::Url::parse(raw).context("Invalid URL")?;
    ensure!(
        u.scheme() == "https"
            && u.username().is_empty()
            && u.password().is_none()
            && u.port_or_known_default() == Some(443),
        "Only credential-free public HTTPS on port 443 is supported"
    );
    ensure!(
        u.host_str().is_some_and(|h| h != "localhost"
            && !h.ends_with(".localhost")
            && !h.ends_with(".local")),
        "Local destinations are not allowed"
    );
    Ok(u)
}
fn readable(html: &str) -> String {
    // Extraction only: never render/execute HTML, fetch subresources or run JS.
    let lower = html.to_ascii_lowercase();
    let mut out = String::new();
    let mut i = 0;
    while i < html.len() {
        if html.as_bytes()[i] == b'<' {
            if let Some(end) = html[i..].find('>') {
                let tag = lower[i + 1..i + end]
                    .split_whitespace()
                    .next()
                    .unwrap_or("");
                if matches!(tag, "script" | "style" | "noscript") {
                    if let Some(close) = lower[i + end + 1..].find(&format!("</{tag}")) {
                        i = i + end + 1 + close;
                        continue;
                    }
                    break;
                }
                out.push(' ');
                i += end + 1;
                continue;
            }
        }
        let c = html[i..].chars().next().unwrap();
        out.push(c);
        i += c.len_utf8();
    }
    out.split_whitespace().collect::<Vec<_>>().join(" ")
}
pub async fn fetch(args: &Value) -> Result<Value> {
    let mut target = url(args["url"].as_str().context("URL required")?)?;
    tokio::time::timeout(Duration::from_secs(20),async {
        for _ in 0..=3 {
            let host=target.host_str().context("Host required")?.to_owned();
            let addresses:Vec<SocketAddr>=tokio::net::lookup_host((host.as_str(),443)).await.context("DNS resolution failed")?.collect();
            ensure!(!addresses.is_empty()&&addresses.len()<=32&&addresses.iter().all(|s|public(s.ip())),"Destination is not a public network address");
            // Pin direct DNS. A configured platform proxy is trusted to enforce its
            // own destination policy; never accept a proxy from model arguments.
            let client=reqwest::Client::builder().redirect(reqwest::redirect::Policy::none())
                .resolve_to_addrs(&host,&addresses).timeout(Duration::from_secs(15)).build()?;
            let mut response=client.get(target.clone()).header("Accept","text/html, text/plain, application/json").send().await.map_err(|_|anyhow::anyhow!("HTTPS request failed"))?;
            if response.status().is_redirection() {
                let location=response.headers().get("location").context("Redirect lacks Location")?.to_str().context("Invalid redirect")?;
                target=url(target.join(location).context("Invalid redirect URL")?.as_str())?;continue;
            }
            ensure!(response.status().is_success(),"HTTPS returned status {}",response.status().as_u16());
            let kind=response.headers().get("content-type").and_then(|v|v.to_str().ok()).unwrap_or("text/plain").chars().take(200).collect::<String>();
            ensure!(kind.starts_with("text/")||kind.starts_with("application/json"),"Only text/HTML/JSON responses are supported");
            let mut data=vec![];let mut truncated=false;
            while let Some(chunk)=response.chunk().await.map_err(|_|anyhow::anyhow!("Response read failed"))? {
                let remaining=512000usize.saturating_sub(data.len());data.extend_from_slice(&chunk[..chunk.len().min(remaining)]);
                if chunk.len()>remaining {truncated=true;break;}
            }
            let raw=String::from_utf8_lossy(&data);let text=if kind.starts_with("text/html"){readable(&raw)}else{raw.into_owned()};
            let output=text.chars().take(16000).collect::<String>();truncated|=output.len()<text.len();
            return Ok(json!({"url":target.as_str(),"status":response.status().as_u16(),"content_type":kind,"text":output,"truncated":truncated,"source":"untrusted_web_content"}));
        } bail!("Redirect limit exceeded")
    }).await.context("Web fetch timed out")?
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_private_and_credential_urls() {
        for ip in [
            "127.0.0.1",
            "169.254.169.254",
            "10.0.0.1",
            "::1",
            "fc00::1",
            "::ffff:127.0.0.1",
            "2002:7f00:1::",
            "2001::1",
            "2001:db8::1",
            "3fff::1",
        ] {
            assert!(!public(ip.parse().unwrap()));
        }
        assert!(public("1.1.1.1".parse().unwrap()));
        assert!(public("2606:4700:4700::1111".parse().unwrap()));
        for u in [
            "http://example.com",
            "https://user:secret@example.com",
            "https://localhost",
            "file:///tmp/x",
        ] {
            assert!(url(u).is_err());
        }
    }
    #[test]
    fn extracts_without_active_content() {
        assert_eq!(
            readable("<h1>Hello</h1><script>bad()</script><p>world</p>"),
            "Hello world"
        );
    }
}
