//! Immutable session-owned model inputs. Bytes never enter journals or RPC replies.
use anyhow::{ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
pub const CHUNK: usize = 256 * 1024;
pub const MAX_FILE: usize = 8 * 1024 * 1024;
pub const MAX_SESSION: usize = 64 * 1024 * 1024;
pub const MAX_SESSION_FILES: usize = 128;
pub const MAX_TURN: usize = 16 * 1024 * 1024;
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Metadata {
    pub id: String,
    pub name: String,
    pub media_type: String,
    pub size: usize,
    pub sha256: String,
}
impl Metadata {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            cfg!(feature = "attachments"),
            "Attachments are not included in this build"
        );
        component(&self.id)?;
        ensure!(
            !self.name.is_empty()
                && self.name.len() <= 240
                && !self.name.chars().any(char::is_control)
                && !self.name.contains(['/', '\\']),
            "Invalid attachment name"
        );
        ensure!(
            self.size > 0 && self.size <= MAX_FILE,
            "Attachment size limit exceeded"
        );
        ensure!(
            self.sha256.len() == 64
                && self
                    .sha256
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
            "Invalid attachment checksum"
        );
        ensure!(
            [
                "image/png",
                "image/jpeg",
                "image/webp",
                "image/gif",
                "application/pdf",
                "text/plain"
            ]
            .contains(&self.media_type.as_str()),
            "Unsupported attachment media type"
        );
        Ok(())
    }
}
#[derive(Clone, Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case", deny_unknown_fields)]
pub enum Upload {
    Begin { metadata: Metadata },
    Chunk { offset: usize, data: String },
    Commit,
    Stat,
}
#[derive(Clone)]
pub struct Blob {
    pub metadata: Metadata,
    pub bytes: Vec<u8>,
    pub sealed: bool,
}
impl Blob {
    pub fn reply(&self) -> Value {
        json!({"metadata":self.metadata,"received":self.bytes.len(),"sealed":self.sealed})
    }
}
pub fn apply(id: &str, old: Option<Blob>, request: Upload) -> Result<Blob> {
    ensure!(
        cfg!(feature = "attachments"),
        "Attachments are not included in this build"
    );
    match request {
        Upload::Begin { metadata } => {
            metadata.validate()?;
            ensure!(metadata.id == id, "Attachment ID mismatch");
            if let Some(old) = old {
                ensure!(old.metadata == metadata, "Attachment ID conflict");
                Ok(old)
            } else {
                Ok(Blob {
                    metadata,
                    bytes: vec![],
                    sealed: false,
                })
            }
        }
        other => {
            let mut blob = old.context("Attachment not found in this session")?;
            match other {
                Upload::Chunk { offset, data } => {
                    ensure!(
                        data.len() <= CHUNK.div_ceil(3) * 4,
                        "Attachment chunk too large"
                    );
                    let bytes = STANDARD
                        .decode(&data)
                        .context("Invalid attachment base64")?;
                    ensure!(
                        !bytes.is_empty() && bytes.len() <= CHUNK,
                        "Invalid attachment chunk"
                    );
                    ensure!(
                        offset <= blob.bytes.len()
                            && offset.saturating_add(bytes.len()) <= blob.metadata.size,
                        "Attachment offset or size conflict"
                    );
                    if offset < blob.bytes.len() {
                        ensure!(
                            blob.bytes.get(offset..offset + bytes.len()) == Some(bytes.as_slice()),
                            "Attachment chunk conflict"
                        );
                    } else {
                        ensure!(!blob.sealed, "Attachment is immutable");
                        blob.bytes.extend(bytes);
                    }
                }
                Upload::Commit => {
                    ensure!(
                        blob.bytes.len() == blob.metadata.size
                            && crate::bundle::digest(&blob.bytes) == blob.metadata.sha256,
                        "Attachment checksum or size mismatch"
                    );
                    validate_bytes(&blob.metadata.media_type, &blob.bytes)?;
                    blob.sealed = true;
                }
                Upload::Stat => {}
                Upload::Begin { .. } => unreachable!(),
            };
            Ok(blob)
        }
    }
}
pub fn validate_bytes(mime: &str, b: &[u8]) -> Result<()> {
    let valid = match mime {
        "image/png" => {
            png_static(b)?;
            true
        }
        "image/jpeg" => b.starts_with(&[255, 216, 255]),
        "image/webp" => {
            b.len() >= 12
                && &b[..4] == b"RIFF"
                && &b[8..12] == b"WEBP"
                && !(b.len() > 20 && &b[12..16] == b"VP8X" && b[20] & 2 != 0)
        }
        "image/gif" => {
            gif_static(b)?;
            true
        }
        "application/pdf" => b.starts_with(b"%PDF-"),
        "text/plain" => std::str::from_utf8(b).is_ok() && !b.contains(&0),
        _ => false,
    };
    ensure!(
        valid,
        "Attachment content does not match its media type or encoding"
    );
    Ok(())
}
fn gif_static(b: &[u8]) -> Result<()> {
    ensure!(
        b.len() >= 13 && (b.starts_with(b"GIF87a") || b.starts_with(b"GIF89a")),
        "Invalid GIF"
    );
    let mut i = 13;
    if b[10] & 128 != 0 {
        i += 3 * (1usize << ((b[10] & 7) + 1));
    }
    let mut frames = 0;
    while i < b.len() {
        let tag = b[i];
        i += 1;
        match tag {
            0x3b => break,
            0x21 => {
                i += 1;
                skip_blocks(b, &mut i)?;
            }
            0x2c => {
                frames += 1;
                ensure!(frames == 1, "Animated images are unsupported");
                ensure!(i + 9 <= b.len(), "Invalid GIF");
                let packed = b[i + 8];
                i += 9;
                if packed & 128 != 0 {
                    i += 3 * (1usize << ((packed & 7) + 1));
                }
                i += 1;
                skip_blocks(b, &mut i)?;
            }
            _ => anyhow::bail!("Invalid GIF"),
        }
    }
    ensure!(frames == 1, "Invalid GIF");
    Ok(())
}
fn skip_blocks(b: &[u8], i: &mut usize) -> Result<()> {
    loop {
        let n = *b.get(*i).context("Invalid GIF")? as usize;
        *i += 1;
        if n == 0 {
            return Ok(());
        }
        *i += n;
        ensure!(*i <= b.len(), "Invalid GIF");
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Part {
    Text { text: String },
    Image { attachment_id: String },
    Document { attachment_id: String },
}
pub fn validate_parts(parts: &[Part]) -> Result<()> {
    ensure!(
        cfg!(feature = "attachments"),
        "Attachments are not included in this build"
    );
    ensure!(
        !parts.is_empty() && parts.len() <= 32,
        "Input content must have 1..32 blocks"
    );
    let mut n = 0;
    for p in parts {
        match p {
            Part::Text { text } => {
                n += text.len();
                ensure!(!text.contains('\0'), "Invalid input text");
            }
            Part::Image { attachment_id } | Part::Document { attachment_id } => {
                component(attachment_id)?
            }
        }
    }
    ensure!(n <= 32000, "Input text exceeds 32,000 bytes");
    Ok(())
}
pub fn history(parts: &[Part]) -> Value {
    json!(parts
        .iter()
        .map(|p| match p {
            Part::Text { text } => json!({"type":"input_text","text":text}),
            Part::Image { attachment_id } =>
                json!({"type":"input_attachment","kind":"image","id":attachment_id}),
            Part::Document { attachment_id } =>
                json!({"type":"input_attachment","kind":"document","id":attachment_id}),
        })
        .collect::<Vec<_>>())
}

fn component(s: &str) -> Result<()> {
    ensure!(
        !s.is_empty()
            && s.len() <= 100
            && s.bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-'),
        "Invalid attachment ID"
    );
    Ok(())
}

/// Resolve immutable references only at the provider boundary, never into events.
pub fn hydrate(
    store: &mut dyn crate::store::StateStore,
    sid: &str,
    history: &[Value],
) -> Result<Vec<Value>> {
    if !cfg!(feature = "attachments") {
        ensure!(
            !history.iter().any(|item| item["content"]
                .as_array()
                .is_some_and(|blocks| blocks.iter().any(|b| b["type"] == "input_attachment"))),
            "Attachments are not included in this build"
        );
        return Ok(history.to_vec());
    }
    let mut out = history.to_vec();
    for item in &mut out {
        if item["role"] != "user" {
            continue;
        }
        let Some(blocks) = item["content"].as_array_mut() else {
            continue;
        };
        for block in blocks {
            if block["type"] != "input_attachment" {
                continue;
            }
            let id = block["id"].as_str().context("Missing attachment ID")?;
            let blob = store.input_attachment(sid, id, Upload::Stat)?;
            ensure!(blob.sealed, "Attachment not committed");
            let m = &blob.metadata;
            m.validate()?;
            ensure!(
                m.id == id
                    && blob.bytes.len() == m.size
                    && crate::bundle::digest(&blob.bytes) == m.sha256,
                "Stored attachment integrity check failed"
            );
            *block = if block["kind"] == "image" {
                ensure!(
                    m.media_type.starts_with("image/"),
                    "Attachment type mismatch"
                );
                json!({"type":"input_image","image_url":format!("data:{};base64,{}",m.media_type,STANDARD.encode(&blob.bytes))})
            } else if m.media_type == "text/plain" {
                json!({"type":"input_text","text":format!("Attached text file: {}\n{}",m.name,std::str::from_utf8(&blob.bytes)? )})
            } else {
                ensure!(
                    m.media_type == "application/pdf",
                    "Attachment type mismatch"
                );
                json!({"type":"input_file","filename":m.name,"file_data":format!("data:application/pdf;base64,{}",STANDARD.encode(&blob.bytes))})
            };
        }
    }
    Ok(out)
}
impl crate::runtime::Runtime {
    /// Inspect text blocks independently without changing their relative positions.
    /// Binary scanning is never implied by a successful text guardrail.
    pub async fn prepare_input_content(
        &self,
        sid: &str,
        parts: &[Part],
        safety: Option<&crate::safety::Config>,
    ) -> Result<Vec<Part>> {
        validate_parts(parts)?;
        let session = self.store.lock().unwrap().metadata(sid)?;
        let policy = self
            .safety_defaults
            .merge(&session.safety)?
            .merge(&safety.cloned().unwrap_or_default())?;
        let mut checked = vec![];
        let mut total = 0usize;
        for part in parts {
            checked.push(match part {
                Part::Text { text } => Part::Text {
                    text: self.check_user_input_with(sid, text, safety).await?,
                },
                Part::Image { attachment_id } | Part::Document { attachment_id } => {
                    let blob = self.store.lock().unwrap().input_attachment(
                        sid,
                        attachment_id,
                        Upload::Stat,
                    )?;
                    ensure!(blob.sealed, "Attachment upload is incomplete");
                    total = total.saturating_add(blob.metadata.size);
                    ensure!(total <= MAX_TURN, "Turn attachment byte limit exceeded");
                    let image = matches!(part, Part::Image { .. });
                    ensure!(
                        if image {
                            blob.metadata.media_type.starts_with("image/")
                        } else {
                            ["application/pdf", "text/plain"]
                                .contains(&blob.metadata.media_type.as_str())
                        },
                        "Attachment type mismatch"
                    );
                    let id = if blob.metadata.media_type == "text/plain" {
                        let text = std::str::from_utf8(&blob.bytes)?;
                        let replacement = self.check_user_input_with(sid, text, safety).await?;
                        if replacement.as_bytes() != blob.bytes {
                            let bytes = replacement.into_bytes();
                            let hash = crate::bundle::digest(&bytes);
                            let identity = crate::bundle::digest(&serde_json::to_vec(&json!([
                                blob.metadata.name,
                                blob.metadata.media_type,
                                hash
                            ]))?);
                            let m = Metadata {
                                id: format!("att_{}", identity),
                                sha256: hash,
                                size: bytes.len(),
                                ..blob.metadata.clone()
                            };
                            let mut store = self.store.lock().unwrap();
                            store.input_attachment(
                                sid,
                                &m.id,
                                Upload::Begin {
                                    metadata: m.clone(),
                                },
                            )?;
                            for (i, b) in bytes.chunks(CHUNK).enumerate() {
                                store.input_attachment(
                                    sid,
                                    &m.id,
                                    Upload::Chunk {
                                        offset: i * CHUNK,
                                        data: STANDARD.encode(b),
                                    },
                                )?;
                            }
                            store.input_attachment(sid, &m.id, Upload::Commit)?;
                            m.id
                        } else {
                            attachment_id.clone()
                        }
                    } else {
                        if policy.protects(crate::safety::Phase::UserInput, None)
                            || policy.protects(crate::safety::Phase::ModelInput, None)
                        {
                            return Err(InspectionUnsupported.into());
                        }
                        attachment_id.clone()
                    };
                    if image {
                        Part::Image { attachment_id: id }
                    } else {
                        Part::Document { attachment_id: id }
                    }
                }
            });
        }
        Ok(checked)
    }
}

/// Admission validates ownership and committed state before appending history.
pub fn validate_references(
    parts: &[Part],
    mut lookup: impl FnMut(&str) -> Result<Metadata>,
) -> Result<()> {
    validate_parts(parts)?;
    let mut size = 0usize;
    for p in parts {
        let (id, image) = match p {
            Part::Text { .. } => continue,
            Part::Image { attachment_id } => (attachment_id, true),
            Part::Document { attachment_id } => (attachment_id, false),
        };
        let m = lookup(id)?;
        ensure!(
            if image {
                m.media_type.starts_with("image/")
            } else {
                ["application/pdf", "text/plain"].contains(&m.media_type.as_str())
            },
            "Attachment type mismatch"
        );
        size = size.saturating_add(m.size);
        ensure!(size <= MAX_TURN, "Turn attachment byte limit exceeded");
    }
    Ok(())
}

/// Short text projection for durable run status. Content order lives in history.
pub fn summary(parts: &[Part]) -> String {
    let mut text = parts
        .iter()
        .filter_map(|p| {
            if let Part::Text { text } = p {
                Some(text.as_str())
            } else {
                None
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
    if text.len() > 32000 {
        let mut end = 32000;
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        text.truncate(end);
    }
    if text.trim().is_empty() {
        "Attached content".into()
    } else {
        text
    }
}

#[derive(Debug)]
pub struct InspectionUnsupported;
impl std::fmt::Display for InspectionUnsupported {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(
            "Binary attachments cannot be inspected by configured text-only input guardrails",
        )
    }
}
impl std::error::Error for InspectionUnsupported {}

fn png_static(bytes: &[u8]) -> Result<()> {
    ensure!(bytes.starts_with(b"\x89PNG\r\n\x1a\n"), "Invalid PNG");
    let mut offset = 8;
    while offset + 12 <= bytes.len() {
        let length = u32::from_be_bytes(bytes[offset..offset + 4].try_into()?) as usize;
        let end = offset
            .checked_add(12)
            .and_then(|n| n.checked_add(length))
            .context("Invalid PNG")?;
        ensure!(end <= bytes.len(), "Invalid PNG");
        let kind = &bytes[offset + 4..offset + 8];
        ensure!(kind != b"acTL", "Animated images are unsupported");
        if kind == b"IEND" {
            return Ok(());
        }
        offset = end;
    }
    anyhow::bail!("Invalid PNG")
}
