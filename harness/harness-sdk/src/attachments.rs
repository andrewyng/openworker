//! User attachments are uploaded in bounded chunks; accepted messages store references.
use crate::{Error, HarnessClient, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
use std::{io::Read, path::Path};
#[derive(Clone)]
pub struct Attachment {
    name: String,
    media_type: String,
    bytes: Vec<u8>,
}
impl Attachment {
    pub fn from_bytes(
        name: impl Into<String>,
        media_type: impl Into<String>,
        bytes: Vec<u8>,
    ) -> Result<Self> {
        let a = Self {
            name: name.into(),
            media_type: media_type.into(),
            bytes,
        };
        a.metadata()?
            .validate()
            .map_err(|e| Error::InvalidInput(e.to_string()))?;
        openworker_runtime::attachments::validate_bytes(&a.media_type, &a.bytes)
            .map_err(|e| Error::InvalidInput(e.to_string()))?;
        Ok(a)
    }
    pub fn from_file(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let name = path
            .file_name()
            .and_then(|s| s.to_str())
            .ok_or_else(|| Error::InvalidInput("Invalid attachment filename".into()))?;
        let ext = path
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_lowercase();
        let mime = match ext.as_str() {
            "png" => "image/png",
            "jpg" | "jpeg" => "image/jpeg",
            "gif" => "image/gif",
            "webp" => "image/webp",
            "pdf" => "application/pdf",
            _ => "text/plain",
        };
        let metadata = std::fs::metadata(path)
            .map_err(|_| Error::InvalidInput("Cannot read attachment".into()))?;
        if !metadata.is_file() || metadata.len() > openworker_runtime::attachments::MAX_FILE as u64
        {
            return Err(Error::InvalidInput(
                "Attachment must be a bounded regular file".into(),
            ));
        }
        let mut bytes = vec![];
        std::fs::File::open(path)
            .map_err(|_| Error::InvalidInput("Cannot read attachment".into()))?
            .take((openworker_runtime::attachments::MAX_FILE + 1) as u64)
            .read_to_end(&mut bytes)
            .map_err(|_| Error::InvalidInput("Cannot read attachment".into()))?;
        Self::from_bytes(name, mime, bytes)
    }
    fn metadata(&self) -> Result<openworker_runtime::attachments::Metadata> {
        let sha256 = openworker_runtime::bundle::digest(&self.bytes);
        let id = openworker_runtime::bundle::digest(
            &serde_json::to_vec(&json!([self.name, self.media_type, sha256])).unwrap(),
        );
        Ok(openworker_runtime::attachments::Metadata {
            id: format!("att_{id}"),
            name: self.name.clone(),
            media_type: self.media_type.clone(),
            size: self.bytes.len(),
            sha256,
        })
    }
    async fn stage(
        &self,
        destination: &Destination<'_>,
        sid: &str,
    ) -> Result<openworker_runtime::attachments::Part> {
        let m = self.metadata()?;
        destination.check()?;
        let call = |upload: Value| json!({"session_id":sid,"id":m.id,"upload":upload});
        let receipt = destination
            .request(call(json!({"operation":"begin","metadata":m})))
            .await?;
        if receipt["sealed"] != true {
            for (i, chunk) in self
                .bytes
                .chunks(openworker_runtime::attachments::CHUNK)
                .enumerate()
            {
                destination.request(call(json!({"operation":"chunk","offset":i*openworker_runtime::attachments::CHUNK,"data":STANDARD.encode(chunk)}))).await?;
            }
            destination
                .request(call(json!({"operation":"commit"})))
                .await?;
        }
        Ok(if self.media_type.starts_with("image/") {
            openworker_runtime::attachments::Part::Image {
                attachment_id: m.id,
            }
        } else {
            openworker_runtime::attachments::Part::Document {
                attachment_id: m.id,
            }
        })
    }
}
#[derive(Clone)]
pub enum InputContent {
    Text(String),
    Attachment(Attachment),
    Reference(openworker_runtime::attachments::Part),
}
impl InputContent {
    pub fn text(text: impl Into<String>) -> Self {
        Self::Text(text.into())
    }
}
impl From<Attachment> for InputContent {
    fn from(a: Attachment) -> Self {
        Self::Attachment(a)
    }
}
#[derive(Clone)]
pub struct UserInput {
    pub content: Vec<InputContent>,
}
impl UserInput {
    pub fn new(content: Vec<InputContent>) -> Self {
        Self { content }
    }
    pub(crate) async fn upload(
        &self,
        client: &HarnessClient,
        sid: &str,
    ) -> Result<Vec<openworker_runtime::attachments::Part>> {
        self.stage(Destination::Managed(client), sid).await
    }
    pub(crate) async fn upload_service(
        &self,
        service: &crate::service::Service,
        sid: &str,
    ) -> Result<Vec<openworker_runtime::attachments::Part>> {
        self.stage(Destination::Service(service), sid).await
    }
    async fn stage(
        &self,
        destination: Destination<'_>,
        sid: &str,
    ) -> Result<Vec<openworker_runtime::attachments::Part>> {
        if self.content.is_empty() || self.content.len() > 32 {
            return Err(Error::InvalidInput(
                "Input content must have 1..32 blocks".into(),
            ));
        }
        let mut total = 0usize;
        let mut preview = vec![];
        for p in &self.content {
            preview.push(match p {
                InputContent::Text(text) => {
                    openworker_runtime::attachments::Part::Text { text: text.clone() }
                }
                InputContent::Attachment(a) => {
                    total = total.saturating_add(a.bytes.len());
                    if a.media_type.starts_with("image/") {
                        openworker_runtime::attachments::Part::Image {
                            attachment_id: a.metadata()?.id,
                        }
                    } else {
                        openworker_runtime::attachments::Part::Document {
                            attachment_id: a.metadata()?.id,
                        }
                    }
                }
                InputContent::Reference(p) => p.clone(),
            });
        }
        if total > openworker_runtime::attachments::MAX_TURN {
            return Err(Error::InvalidInput(
                "Turn attachment byte limit exceeded".into(),
            ));
        }
        openworker_runtime::attachments::validate_parts(&preview)
            .map_err(|e| Error::InvalidInput(e.to_string()))?;
        let mut parts = vec![];
        for p in &self.content {
            parts.push(match p {
                InputContent::Text(text) => {
                    openworker_runtime::attachments::Part::Text { text: text.clone() }
                }
                InputContent::Attachment(a) => a.stage(&destination, sid).await?,
                InputContent::Reference(p) => p.clone(),
            });
        }
        openworker_runtime::attachments::validate_parts(&parts)
            .map_err(|e| Error::InvalidInput(e.to_string()))?;
        Ok(parts)
    }
}

enum Destination<'a> {
    Managed(&'a HarnessClient),
    Service(&'a crate::service::Service),
}
impl Destination<'_> {
    fn check(&self) -> Result<()> {
        let supported = match self {
            Self::Managed(c) => c.info()["capabilities"]["attachments"] == true,
            Self::Service(c) => c.capabilities()["features"]["attachments"] == true,
        };
        if supported {
            Ok(())
        } else {
            Err(Error::Setup("Host does not support attachments".into()))
        }
    }
    async fn request(&self, p: Value) -> Result<Value> {
        match self {
            Self::Managed(c) => c.request("attachment/upload", p).await,
            Self::Service(s) => s
                .json(
                    "POST",
                    &format!(
                        "/v1/sessions/{}/attachments/{}",
                        p["session_id"].as_str().unwrap(),
                        p["id"].as_str().unwrap()
                    ),
                    Some(p["upload"].clone()),
                    None,
                )
                .await
                .map_err(|e| {
                    if e.code == "outcome_unknown" {
                        Error::OutcomeUnknown
                    } else {
                        Error::Remote(e.to_string())
                    }
                }),
        }
    }
}
