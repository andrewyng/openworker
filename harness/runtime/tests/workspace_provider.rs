#![cfg(feature = "workspace-reference")]
use openworker_runtime::workspaces::{reference::ReferenceProvider, *};
use serde_json::{json, Value};
use std::sync::Arc;

fn request(key: &str, data: Option<&str>) -> GetOrCreateWorkspace {
    serde_json::from_value(json!({"key":key,"dataId":data})).unwrap()
}
fn opts(v: Value) -> ProviderOptions {
    serde_json::from_value(v).unwrap()
}

#[tokio::test]
async fn identities_survive_concurrent_creation_and_recovery() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("one.db");
    let mut registry = Registry::default();
    for (name, db) in [("one", path.clone()), ("two", dir.path().join("two.db"))] {
        registry
            .register(name, Arc::new(ReferenceProvider::open(&db).unwrap()))
            .unwrap();
    }
    assert!(registry
        .register("one", Arc::new(ReferenceProvider::open(&path).unwrap()))
        .is_err());
    assert!(registry.provider("missing").is_err());
    let p = registry.provider("one").unwrap();
    let c = p.start(opts(json!({"podProfile":"small"}))).await.unwrap();
    let other = registry
        .provider("two")
        .unwrap()
        .start(Default::default())
        .await
        .unwrap();
    let (a, b) = tokio::join!(
        Registry::open("one", &*c, request("site", None)),
        Registry::open("one", &*c, request("site", None))
    );
    let a = a.unwrap();
    assert_eq!(a, b.unwrap());
    assert_ne!(
        a,
        Registry::open("two", &*other, request("site", None))
            .await
            .unwrap()
    );
    assert!(
        Registry::open("two", &*other, request("x", Some(&a.data_id)))
            .await
            .is_err()
    );
    assert!(
        Registry::open("one", &*c, request("new", Some("one.absent")))
            .await
            .is_err()
    );
    let foreign = Registry::open("one", &*c, request("other", None))
        .await
        .unwrap();
    let (first, second) = tokio::join!(
        Registry::open("one", &*c, request("race", Some(&a.data_id))),
        Registry::open("one", &*c, request("race", Some(&foreign.data_id)))
    );
    assert_ne!(first.is_ok(), second.is_ok());
    assert!(
        Registry::open("one", &*c, request("site", Some(&foreign.data_id)))
            .await
            .is_err()
    );
    let mut review = request("review", Some(&a.data_id));
    review.options = opts(json!({"podProfile":"medium"}));
    let review = Registry::open("one", &*c, review).await.unwrap();
    assert_eq!(review.data_id, a.data_id);
    assert_ne!(review.workspace_id, a.workspace_id);
    let (client, id) = registry.recover(&a.workspace_id).await.unwrap();
    client.stop_workspace(id.clone()).await.unwrap();
    let recovered = ReferenceProvider::open(&path)
        .unwrap()
        .start(Default::default())
        .await
        .unwrap();
    let next = Registry::open("one", &*recovered, request("site", None))
        .await
        .unwrap();
    assert_eq!(a.data_id, next.data_id);
    assert_ne!(a.workspace_id, next.workspace_id);
    assert!(registry.recover(&a.workspace_id).await.is_err());
    assert!(p
        .start(opts(json!({"token":"secret-sentinel"})))
        .await
        .err()
        .is_some_and(|e| !e.to_string().contains("sentinel")));
}

#[tokio::test]
async fn session_scoped_file_access_and_deletion() -> anyhow::Result<()> {
    let dir = tempfile::tempdir().unwrap();
    let provider = ReferenceProvider::open(&dir.path().join("db")).unwrap();
    let c = provider.start(Default::default()).await.unwrap();
    let a = c.get_or_create(request("a", None)).await.unwrap();
    let b = c
        .get_or_create(request("b", Some(&a.data_id)))
        .await
        .unwrap();
    let w = a.workspace_id.clone();
    let v = b.workspace_id.clone();
    c.attach_session(w.clone(), "builder".into()).await.unwrap();
    c.attach_session(w.clone(), "peer".into()).await.unwrap();
    c.attach_session(v.clone(), "review".into()).await.unwrap();
    let attachment = |id: &str, sid: &str| WorkspaceAttachment {
        workspace_id: id.into(),
        session_id: sid.into(),
    };
    let file = |id: &str, sid: &str, op| c.file(attachment(id, sid), op);
    file(
        &w,
        "builder",
        FileOperation::WriteFile {
            path: "index.html".into(),
            content: "Hello".into(),
        },
    )
    .await
    .unwrap();
    let read = || FileOperation::ReadFile {
        path: "index.html".into(),
    };
    assert_eq!(file(&v, "review", read()).await.unwrap()["text"], "Hello");
    assert!(file(&w, "review", read()).await.is_err());
    #[cfg(unix)]
    {
        // The reference provider addresses SQLite rows only. Even a real link
        // beside its database must never resolve to a host file through a tool.
        let sentinel = dir.path().join("private.txt");
        std::fs::write(&sentinel, "private")?;
        std::os::unix::fs::symlink(&sentinel, dir.path().join("link"))?;
        assert!(file(
            &w,
            "builder",
            FileOperation::ReadFile {
                path: "link".into()
            }
        )
        .await
        .is_err());
        file(
            &w,
            "builder",
            FileOperation::WriteFile {
                path: "link".into(),
                content: "virtual".into(),
            },
        )
        .await?;
        assert_eq!(std::fs::read_to_string(&sentinel)?, "private");
    }
    for path in ["../outside", "/outside", "assets/../outside", "bad\\path"] {
        assert!(file(
            &v,
            "review",
            FileOperation::WriteFile {
                path: path.into(),
                content: "bad".into()
            }
        )
        .await
        .is_err());
    }
    assert!(c.stop_workspace(w.clone()).await.is_err());
    c.detach_session(attachment(&w, "builder")).await.unwrap();
    c.detach_session(attachment(&w, "builder")).await.unwrap();
    assert!(c.attach_session(w.clone(), "builder".into()).await.is_err());
    assert!(file(&w, "builder", read()).await.is_err());
    assert_eq!(file(&w, "peer", read()).await.unwrap()["text"], "Hello");
    c.detach_session(attachment(&w, "peer")).await.unwrap();
    c.stop_workspace(w).await.unwrap();
    assert!(c.delete_data(a.data_id.clone()).await.is_err());
    file(
        &v,
        "review",
        FileOperation::EditFile {
            path: "index.html".into(),
            old_text: "Hello".into(),
            new_text: "Changed".into(),
        },
    )
    .await
    .unwrap();
    assert_eq!(
        file(&v, "review", FileOperation::ListFiles {})
            .await
            .unwrap()["files"],
        if cfg!(unix) {
            json!(["index.html", "link"])
        } else {
            json!(["index.html"])
        }
    );
    c.detach_session(attachment(&v, "review")).await.unwrap();
    c.stop_workspace(v).await.unwrap();
    c.delete_data(a.data_id.clone()).await.unwrap();
    let replacement = c.get_or_create(request("a", None)).await.unwrap();
    assert_ne!(replacement.data_id, a.data_id);
    c.delete_data(a.data_id).await.unwrap();
    c.attach_session(replacement.workspace_id, "fresh".into())
        .await
        .unwrap();
    Ok(())
}
