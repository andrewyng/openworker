use super::*;
use std::{
    io::{Read, Write},
    net::TcpListener,
    sync::{Arc, Mutex},
    thread,
};
struct Reply {
    status: u16,
    body: Vec<u8>,
    headers: String,
}
fn reply(v: Value) -> Reply {
    Reply {
        status: 200,
        body: serde_json::to_vec(&v).unwrap(),
        headers: String::new(),
    }
}
fn server(responses: Vec<Reply>) -> (String, Arc<Mutex<Vec<String>>>, thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = format!("http://{}", listener.local_addr().unwrap());
    let records = Arc::new(Mutex::new(vec![]));
    let saved = records.clone();
    let thread = thread::spawn(move || {
        for response in responses {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut bytes = vec![];
            let mut buf = [0; 8192];
            let mut needed = None;
            loop {
                let n = stream.read(&mut buf).unwrap();
                if n == 0 {
                    break;
                }
                bytes.extend_from_slice(&buf[..n]);
                if let Some(end) = bytes.windows(4).position(|s| s == b"\r\n\r\n") {
                    let header = String::from_utf8_lossy(&bytes[..end]);
                    let length = header
                        .lines()
                        .find_map(|l| {
                            l.to_ascii_lowercase()
                                .strip_prefix("content-length:")
                                .map(|s| s.trim().parse::<usize>().unwrap())
                        })
                        .unwrap_or(0);
                    needed = Some(end + 4 + length);
                }
                if needed.is_some_and(|total| bytes.len() >= total) {
                    break;
                }
                assert!(bytes.len() < 2 * 1024 * 1024);
            }
            saved
                .lock()
                .unwrap()
                .push(String::from_utf8_lossy(&bytes).into_owned());
            if response.status == 0 {
                continue;
            }
            let header=format!("HTTP/1.1 {} Fixture\r\nContent-Length: {}\r\nContent-Type: application/json\r\nConnection: close\r\n{}\r\n",response.status,response.body.len(),response.headers);
            let _ = stream.write_all(header.as_bytes());
            let _ = stream.write_all(&response.body);
        }
    });
    (address, records, thread)
}
fn api(base: String) -> Google {
    let mut g = Google::new(
        super::super::lifecycle_tests::control(),
        Zeroizing::new("fixture-not-a-real-token".into()),
    )
    .unwrap();
    g.test_base = Some(base);
    g
}
fn operation(c: &Control, verb: &str) -> Value {
    json!({"name":format!("{}/operations/{verb}-1",c.parent()),"metadata":{"target":c.deployment(),"verb":verb,"createTime":"2026-10-30T14:00:00Z"},"done":false})
}
#[tokio::test]
async fn transport_uses_create_only_and_delete_underlying_resources_with_fixed_request_ids() {
    let c = super::super::lifecycle_tests::control();
    let (base, records, t) = server(vec![
        reply(operation(&c, "create")),
        reply(operation(&c, "delete")),
    ]);
    let mut g = api(base);
    g.create().await.unwrap();
    let id = uuid::Uuid::new_v4().to_string();
    g.delete(&id).await.unwrap();
    t.join().unwrap();
    let r = records.lock().unwrap();
    assert_eq!(r.len(), 2);
    assert!(r[0].starts_with(&format!("POST /v1/{}/deployments?", c.parent())));
    assert!(r[0].contains(&format!("requestId={}", g.control.request_id)));
    assert!(!r[0].contains("PATCH"));
    let request: Value = serde_json::from_str(r[0].split("\r\n\r\n").nth(1).unwrap()).unwrap();
    assert_eq!(request, c.request());
    assert!(r[1].starts_with(&format!("DELETE /v1/{}?", c.deployment())));
    assert!(r[1].contains("deletePolicy=DELETE"));
    assert!(r[1].contains("force=true"));
    assert!(r[1].contains(&format!("requestId={id}")));
    assert!(!r[1].contains("ABANDON"));
}
#[tokio::test]
async fn real_http_source_and_checkpoint_paths_pin_generation_hash_and_cas() {
    let c = super::super::lifecycle_tests::control();
    let bytes = source_zip(&c.plan).unwrap();
    let bucket = json!({"name":c.bucket(),"projectNumber":c.plan.gcp_bootstrap.as_ref().unwrap().project_number,"iamConfiguration":{"uniformBucketLevelAccess":{"enabled":true},"publicAccessPrevention":"enforced"},"versioning":{"enabled":true}});
    let source_meta = json!({"bucket":c.bucket(),"name":c.source_object(),"generation":c.source_generation,"size":bytes.len().to_string()});
    let checkpoint_meta =
        json!({"bucket":c.bucket(),"name":c.checkpoint_object(),"generation":"124"});
    let (base, records, t) = server(vec![
        reply(bucket),
        reply(source_meta),
        Reply {
            status: 200,
            body: bytes,
            headers: String::new(),
        },
        reply(checkpoint_meta),
    ]);
    let mut g = api(base);
    g.source().await.unwrap();
    let j = Checkpoint::new(&c).unwrap();
    assert_eq!(g.save("123", &j).await.unwrap(), "124");
    t.join().unwrap();
    let r = records.lock().unwrap();
    assert!(r[1].contains("generation=123"));
    assert!(r[2].contains("generation=123"));
    assert!(r[2].contains("alt=media"));
    assert!(r[3].contains("ifGenerationMatch=123"));
    assert!(r[3].contains("uploadType=media"));
    let sent: Checkpoint = serde_json::from_str(r[3].split("\r\n\r\n").nth(1).unwrap()).unwrap();
    assert_eq!(sent.control_sha256, c.sha().unwrap());
}
#[tokio::test]
async fn http_denial_unknown_status_redirects_and_oversized_bodies_are_not_absence_or_retried() {
    let (base, records, t) = server(vec![Reply {
        status: 0,
        body: vec![],
        headers: String::new(),
    }]);
    let mut lost = api(base);
    assert!(lost
        .create()
        .await
        .unwrap_err()
        .to_string()
        .contains("unknown"));
    t.join().unwrap();
    assert_eq!(records.lock().unwrap().len(), 1);
    for status in [403u16, 412, 429, 503] {
        let (base, records, t) = server(vec![Reply {
            status,
            body: b"DO_NOT_EXPORT_SECRET_PROVIDER_BODY".to_vec(),
            headers: String::new(),
        }]);
        let mut g = api(base);
        let error = g.deployment().await.unwrap_err().to_string();
        assert!(error.contains(&status.to_string()));
        assert!(!error.contains("DO_NOT_EXPORT"));
        t.join().unwrap();
        assert_eq!(records.lock().unwrap().len(), 1);
    }
    let sink = TcpListener::bind("127.0.0.1:0").unwrap();
    sink.set_nonblocking(true).unwrap();
    let (base, _, t) = server(vec![Reply {
        status: 302,
        body: vec![],
        headers: format!(
            "Location: http://{}/steal-token\r\n",
            sink.local_addr().unwrap()
        ),
    }]);
    let mut g = api(base);
    assert!(g.deployment().await.is_err());
    t.join().unwrap();
    assert!(sink.accept().is_err());
    let (base, _, t) = server(vec![Reply {
        status: 200,
        body: vec![b'x'; 1024 * 1024 + 1],
        headers: String::new(),
    }]);
    let mut g = api(base);
    assert!(g
        .deployment()
        .await
        .unwrap_err()
        .to_string()
        .contains("limit"));
    t.join().unwrap();
}
#[tokio::test]
async fn pagination_truncation_repeated_tokens_and_generation_replacement_fail_closed() {
    let (base, records, t) = server(vec![
        reply(json!({"operations":[],"nextPageToken":"repeat"})),
        reply(json!({"operations":[],"nextPageToken":"repeat"})),
    ]);
    let mut g = api(base);
    assert!(g.operations().await.is_err());
    t.join().unwrap();
    assert_eq!(records.lock().unwrap().len(), 2);
    let (base, _, t) = server(vec![reply(
        json!({"operations":[],"unreachable":["region"]}),
    )]);
    let mut g = api(base);
    assert!(g.operations().await.is_err());
    t.join().unwrap();
    let c = super::super::lifecycle_tests::control();
    let (base, _, t) = server(vec![reply(
        json!({"bucket":c.bucket(),"name":c.source_object(),"generation":"124","size":"1"}),
    )]);
    let g = api(base);
    assert!(g
        .object(&c.source_object(), Some("123"), 1024)
        .await
        .is_err());
    t.join().unwrap();
}
fn guard(c: &Control) -> Value {
    json!({"name":c.job(),"uid":uuid::Uuid::new_v4().to_string(),"generation":"1","observedGeneration":"1","terminalCondition":{"state":"CONDITION_SUCCEEDED"},"template":{"taskCount":1,"parallelism":1,"template":{"serviceAccount":accounts(&c.plan)["cleanup"],"maxRetries":0,"timeout":"180s","containers":[{"image":c.guard_image,"command":["/opt/openworker/ows"],"args":super::super::control_export::tick_args(c).unwrap(),"resources":{"limits":{"cpu":"1","memory":"512Mi"}}}]}}})
}
fn schedule(c: &Control) -> Value {
    json!({"name":c.job(),"state":"ENABLED","schedule":"*/1 * * * *","timeZone":"Etc/UTC","attemptDeadline":"180s","httpTarget":{"uri":format!("https://run.googleapis.com/v2/{}:run",c.job()),"httpMethod":"POST","body":"e30=","headers":{"Content-Type":"application/json"},"oauthToken":{"serviceAccountEmail":accounts(&c.plan)["cleanup"],"scope":"https://www.googleapis.com/auth/cloud-platform"}}})
}
#[tokio::test]
async fn guard_health_checks_real_get_shapes_and_rejects_image_auth_and_override_drift() {
    let c = super::super::lifecycle_tests::control();
    let job = guard(&c);
    let cron = schedule(&c);
    let (base, _, t) = server(vec![reply(job.clone()), reply(cron.clone())]);
    let mut g = api(base);
    assert_eq!(g.guard().await.unwrap(), job["uid"].as_str().unwrap());
    t.join().unwrap();
    for key in [
        "image",
        "identity",
        "generation",
        "arguments",
        "environment",
    ] {
        let mut bad = job.clone();
        match key {
            "image" => {
                bad["template"]["template"]["containers"][0]["image"] = json!("other:latest")
            }
            "identity" => bad["template"]["template"]["serviceAccount"] = json!("other"),
            "generation" => bad["observedGeneration"] = Value::Null,
            "arguments" => bad["template"]["template"]["containers"][0]["args"] = json!(["other"]),
            _ => {
                bad["template"]["template"]["containers"][0]["env"] =
                    json!([{"name":"EXTRA","value":"bad"}])
            }
        };
        let (base, _, t) = server(vec![reply(bad)]);
        let mut g = api(base);
        assert!(g.guard().await.is_err(), "{key}");
        t.join().unwrap();
    }
    for key in ["body", "identity", "state", "target"] {
        let mut bad = cron.clone();
        match key {
            "body" => bad["httpTarget"]["body"] = json!("overrides"),
            "identity" => bad["httpTarget"]["oauthToken"]["serviceAccountEmail"] = json!("other"),
            "state" => bad["state"] = json!("PAUSED"),
            _ => bad["httpTarget"]["uri"] = json!("https://other.example"),
        };
        let (base, _, t) = server(vec![reply(job.clone()), reply(bad)]);
        let mut g = api(base);
        assert!(g.guard().await.is_err(), "{key}");
        t.join().unwrap();
    }
}
#[tokio::test]
async fn production_creation_gate_is_closed_without_http_or_claiming_live_qualification() {
    let (base, records, t) = server(vec![]);
    let mut g = api(base);
    assert!(g.authorize_create().await.is_err());
    t.join().unwrap();
    assert!(records.lock().unwrap().is_empty());
}

#[tokio::test]
async fn dependent_inventory_pins_compute_ids_without_trusting_server_urls_or_deleting_assets() {
    let c = super::super::lifecycle_tests::control();
    let p = project(&c.plan);
    let cluster = format!("{}-gke", c.prefix());
    let base_path =
        format!("https://www.googleapis.com/compute/v1/projects/{p}/zones/us-central1-a");
    let vm = format!("{base_path}/instances/node");
    let boot = format!("{base_path}/disks/boot");
    let pvc = format!("{base_path}/disks/pvc");
    let mig = format!("{base_path}/instanceGroupManagers/group");
    let ig = format!("{base_path}/instanceGroups/group");
    let template = format!(
        "https://www.googleapis.com/compute/v1/projects/{p}/global/instanceTemplates/template"
    );
    let vm_list = json!({"items":[{"selfLink":vm,"labels":{"goog-k8s-cluster-name":cluster},"disks":[{"source":boot}],"metadata":{"items":[{"key":"created-by","value":mig}]}}]});
    let (base, records, t) = server(vec![
        Reply {
            status: 404,
            body: vec![],
            headers: String::new(),
        },
        reply(vm_list),
        reply(json!({"id":"101"})),
        reply(json!({"id":"102"})),
        reply(json!({"items":[{"selfLink":pvc,"labels":{"goog-k8s-cluster-name":cluster}}]})),
        reply(json!({"id":"103"})),
        reply(json!({"id":"104","instanceGroup":ig,"instanceTemplate":template})),
        reply(json!({"id":"105"})),
        reply(json!({"id":"106"})),
    ]);
    let mut g = api(base);
    let assets = g.dependent_assets().await.unwrap();
    assert_eq!(assets.len(), 6);
    for a in &assets {
        a.validate(&c).unwrap();
    }
    t.join().unwrap();
    assert!(records
        .lock()
        .unwrap()
        .iter()
        .all(|r| r.starts_with("GET ")));
    assert!(g
        .asset_path("https://attacker.example/compute/v1/projects/p/zones/z/disks/x")
        .is_err());
    assert!(g
        .asset_path(
            "https://www.googleapis.com/compute/v1/projects/foreign/zones/us-central1-a/disks/x"
        )
        .is_err());
    let (base, _, t) = server(
        (0..6)
            .map(|_| Reply {
                status: 404,
                body: vec![],
                headers: String::new(),
            })
            .collect(),
    );
    let mut g = api(base);
    assert!(g.assets_absent(&assets).await.unwrap());
    t.join().unwrap();
}
