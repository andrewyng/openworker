#![cfg(feature = "attachments")]
use base64::{engine::general_purpose::STANDARD, Engine};
use openworker_runtime::{
    attachments::*,
    store::{Session, StateStore, Store},
    turn_options::Context,
};
use serde_json::json;
fn session(id: &str) -> Session {
    serde_json::from_value(json!({"id":id,"name":"Reader","workspace":"/work","coworker":"reader","bundle_digest":"bundle","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap()
}
fn meta(id: &str, data: &[u8]) -> Metadata {
    Metadata {
        id: id.into(),
        name: "notes.txt".into(),
        media_type: "text/plain".into(),
        size: data.len(),
        sha256: openworker_runtime::bundle::digest(data),
    }
}
fn upload(store: &mut dyn StateStore, sid: &str, id: &str, data: &[u8]) {
    store
        .input_attachment(
            sid,
            id,
            Upload::Begin {
                metadata: meta(id, data),
            },
        )
        .unwrap();
    for (i, b) in data.chunks(CHUNK).enumerate() {
        let r = Upload::Chunk {
            offset: i * CHUNK,
            data: STANDARD.encode(b),
        };
        store.input_attachment(sid, id, r.clone()).unwrap();
        store.input_attachment(sid, id, r).unwrap();
    }
    store.input_attachment(sid, id, Upload::Commit).unwrap();
}
#[test]
fn sqlite_references_are_owned_immutable_recoverable_and_atomic() {
    let root = tempfile::tempdir().unwrap();
    let mut s = Store::open(root.path()).unwrap();
    for id in ["owner", "other"] {
        s.save(&session(id), "session_created", json!({})).unwrap();
    }
    let bytes = vec![65; CHUNK + 32];
    upload(&mut s, "owner", "att_text", &bytes);
    assert!(s
        .input_attachment("other", "att_text", Upload::Stat)
        .is_err());
    assert!(s
        .input_attachment(
            "owner",
            "att_text",
            Upload::Chunk {
                offset: 0,
                data: STANDARD.encode(b"changed")
            }
        )
        .is_err());
    assert!(s
        .input_attachment(
            "owner",
            "att_text",
            Upload::Begin {
                metadata: meta("att_text", b"changed")
            }
        )
        .is_err());
    let parts = vec![
        Part::Text {
            text: "Before".into(),
        },
        Part::Document {
            attachment_id: "att_text".into(),
        },
        Part::Text {
            text: "After".into(),
        },
    ];
    let context = Context {
        content: Some(parts.clone()),
        ..Default::default()
    };
    let c = s.cursor("other").unwrap();
    assert!(s
        .accept_with_context("other", "s", "Read", None, None, context.clone())
        .is_err());
    assert_eq!(s.cursor("other").unwrap(), c);
    let (mut original, fresh, _) = s
        .accept_with_context("owner", "s", "Read", None, None, context.clone())
        .unwrap();
    assert!(fresh);
    let (_, fresh, _) = s
        .accept_with_context("owner", "s", "Read", None, None, context)
        .unwrap();
    assert!(!fresh);
    assert!(s
        .accept_with_context(
            "owner",
            "s",
            "Read",
            None,
            None,
            Context {
                content: Some(vec![parts[1].clone()]),
                ..Default::default()
            }
        )
        .is_err());
    assert_eq!(original.history[0]["content"], history(&parts));
    assert!(!serde_json::to_string(&s.get("owner").unwrap())
        .unwrap()
        .contains(&STANDARD.encode(&bytes)));
    // Compaction context may drop old inputs; the append-only original remains available.
    original
        .history
        .push(json!({"role":"assistant","content":"done"}));
    original.turn.as_mut().unwrap().status = "completed".into();
    s.save(&original, "turn_completed", json!({"status":"completed"}))
        .unwrap();
    drop(s);
    let mut s = Store::open(root.path()).unwrap();
    upload(&mut s, "owner", "orphan", b"orphan");
    let sql = rusqlite::Connection::open(root.path().join("runtime.sqlite3")).unwrap();
    sql.execute("UPDATE input_attachments SET created=0", [])
        .unwrap();
    drop(sql);
    upload(&mut s, "owner", "fresh", b"new upload triggers expiry");
    assert!(s.input_attachment("owner", "orphan", Upload::Stat).is_err());
    assert!(
        s.input_attachment("owner", "att_text", Upload::Stat)
            .unwrap()
            .sealed
    );
    let restored = s.get("owner").unwrap();
    let hydrated = hydrate(&mut s, "owner", &restored.history).unwrap();
    assert_eq!(hydrated[0]["content"][0]["text"], "Before");
    assert!(hydrated[0]["content"][1]["text"]
        .as_str()
        .unwrap()
        .ends_with(&String::from_utf8(bytes.clone()).unwrap()));
    assert_eq!(hydrated[0]["content"][2]["text"], "After");
    assert!(!s
        .events("owner", 0)
        .unwrap()
        .iter()
        .any(|e| e.data.to_string().contains(&STANDARD.encode(&bytes))));
}
#[test]
fn reservations_are_bounded_and_failed_commits_do_not_seal() {
    let root = tempfile::tempdir().unwrap();
    let mut s = Store::open(root.path()).unwrap();
    s.save(&session("s"), "session_created", json!({})).unwrap();
    for i in 0..8 {
        s.input_attachment(
            "s",
            &format!("a{i}"),
            Upload::Begin {
                metadata: Metadata {
                    size: MAX_FILE,
                    ..meta(&format!("a{i}"), b"x")
                },
            },
        )
        .unwrap();
    }
    assert!(s
        .input_attachment(
            "s",
            "overflow",
            Upload::Begin {
                metadata: meta("overflow", b"x")
            }
        )
        .is_err());
    assert!(s.input_attachment("s", "a0", Upload::Commit).is_err());
    assert!(!s.input_attachment("s", "a0", Upload::Stat).unwrap().sealed);
    assert!(s
        .input_attachment(
            "s",
            "a0",
            Upload::Chunk {
                offset: 1,
                data: STANDARD.encode(b"x")
            }
        )
        .is_err());
    assert!(s
        .input_attachment(
            "s",
            "a0",
            Upload::Chunk {
                offset: 0,
                data: STANDARD.encode(vec![65; CHUNK + 1])
            }
        )
        .is_err());
    assert!(s
        .input_attachment(
            "s",
            "a0",
            Upload::Chunk {
                offset: 0,
                data: "%%%".into()
            }
        )
        .is_err());
}
#[test]
fn encoding_and_public_projection_do_not_leak_binary_inputs() {
    assert!(validate_bytes("text/plain", &[0xff]).is_err());
    assert!(validate_bytes("text/plain", b"a\0b").is_err());
    assert!(validate_bytes("application/pdf", b"not a PDF").is_err());
    assert!(meta("a", b"x").validate().is_ok());
    assert!(Metadata {
        name: "../secret".into(),
        ..meta("a", b"x")
    }
    .validate()
    .is_err());
    let private = json!({"role":"user","content":[{"type":"input_text","text":"hello"},{"type":"input_image","image_url":"data:SECRET"},{"type":"input_file","filename":"a.pdf","file_data":"data:PRIVATE"}]});
    let projected = openworker_runtime::model::public_items(&[private]);
    let text = serde_json::to_string(&projected).unwrap();
    assert!(!text.contains("SECRET"));
    assert!(!text.contains("PRIVATE"));
    assert!(text.contains("hello"));
    assert!(text.contains("content_withheld"));
}

#[test]
fn compaction_retains_interleaved_text_and_reference_context() {
    let parts = vec![
        Part::Text {
            text: "Before the image".into(),
        },
        Part::Image {
            attachment_id: "image_reference".into(),
        },
        Part::Text {
            text: "After the image".into(),
        },
    ];
    let raw = vec![
        json!({"role":"user","content":history(&parts)}),
        json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}),
        json!({"role":"user","content":"next"}),
        json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"more"}]}),
    ];
    let candidate =
        openworker_runtime::compaction::candidate(&raw, None, &Default::default(), true).unwrap();
    assert!(candidate.prompt.contains("Before the image"));
    assert!(candidate.prompt.contains("image_reference"));
    assert!(candidate.prompt.contains("After the image"));
    assert_eq!(raw[0]["content"], history(&parts));
}

#[test]
fn small_uploads_are_count_bounded_and_animation_is_rejected() {
    let root = tempfile::tempdir().unwrap();
    let mut s = Store::open(root.path()).unwrap();
    s.save(&session("s"), "session_created", json!({})).unwrap();
    for i in 0..MAX_SESSION_FILES {
        let id = format!("a{i}");
        s.input_attachment(
            "s",
            &id,
            Upload::Begin {
                metadata: meta(&id, b"x"),
            },
        )
        .unwrap();
    }
    assert!(s
        .input_attachment(
            "s",
            "overflow",
            Upload::Begin {
                metadata: meta("overflow", b"x")
            }
        )
        .is_err());
    let mut apng = b"\x89PNG\r\n\x1a\n".to_vec();
    apng.extend(8u32.to_be_bytes());
    apng.extend(b"acTL");
    apng.extend([0; 12]);
    assert!(validate_bytes("image/png", &apng).is_err());
    let mut webp = b"RIFF\0\0\0\0WEBPVP8X\0\0\0\0\x02".to_vec();
    assert!(validate_bytes("image/webp", &webp).is_err());
    webp[20] = 0;
    assert!(validate_bytes("image/webp", &webp).is_ok());
}

#[test]
fn status_summary_is_bounded_without_changing_canonical_content() {
    let parts = vec![
        Part::Text {
            text: "é".repeat(8000),
        },
        Part::Text {
            text: "é".repeat(8000),
        },
    ];
    validate_parts(&parts).unwrap();
    let text = summary(&parts);
    assert!(text.len() <= 32000);
    assert!(text.contains('\n'));
    assert_eq!(history(&parts)[1]["text"].as_str().unwrap().len(), 16000);
}

#[test]
fn closed_sessions_allow_only_sealed_begin_reconciliation() {
    let mut store = Store::memory().unwrap();
    store
        .save(&session("s"), "session_created", json!({}))
        .unwrap();
    upload(&mut store, "s", "notes", b"immutable");
    let mut closed = store.get("s").unwrap();
    closed.closed = true;
    store.save(&closed, "session_closed", json!({})).unwrap();
    assert!(
        store
            .input_attachment(
                "s",
                "notes",
                Upload::Begin {
                    metadata: meta("notes", b"immutable")
                }
            )
            .unwrap()
            .sealed
    );
    assert!(store
        .input_attachment(
            "s",
            "new",
            Upload::Begin {
                metadata: meta("new", b"new")
            }
        )
        .is_err());
    assert!(store
        .input_attachment(
            "s",
            "notes",
            Upload::Chunk {
                offset: 0,
                data: STANDARD.encode(b"immutable")
            }
        )
        .is_err());
}
