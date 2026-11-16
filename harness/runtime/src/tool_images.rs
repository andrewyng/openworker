//! Latest native image per session. Pixels never enter the journal or event stream.
use anyhow::{bail, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use openworker_tools::ToolImage;
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    time::{Duration, Instant},
};

#[derive(Default)]
pub struct Images(HashMap<String, (String, ToolImage, Instant, String)>);
impl Images {
    pub fn clear(&mut self, sid: &str) {
        self.0.remove(sid);
    }
    pub fn owner(&self, sid: &str) -> Option<&str> {
        self.0.get(sid).map(|(_, _, _, tool)| tool.as_str())
    }
    pub fn put(&mut self, sid: &str, tool: &str, images: Vec<ToolImage>) -> Result<Value> {
        self.0
            .retain(|_, (_, _, at, _)| at.elapsed() < Duration::from_secs(300));
        if images.len() != 1 {
            bail!("Native image result must contain exactly one image");
        }
        let image = images.into_iter().next().unwrap();
        if image.base64.len() > 768_000 {
            bail!("Native image exceeds limit");
        }
        let bytes = STANDARD.decode(&image.base64)?;
        if !(image.mime_type == "image/jpeg" && bytes.starts_with(&[255, 216, 255])
            || image.mime_type == "image/png" && bytes.starts_with(b"\x89PNG\r\n\x1a\n"))
        {
            bail!("Unsupported native image format");
        }
        if !self.0.contains_key(sid) && self.0.len() >= 4 {
            bail!("Ephemeral image session limit");
        }
        let id = crate::id("img");
        self.0
            .insert(sid.into(), (id.clone(), image, Instant::now(), tool.into()));
        Ok(json!({"id":id,"retention":"ephemeral_latest_only"}))
    }
    pub fn hydrate(&mut self, sid: &str, history: &[Value]) -> Vec<Value> {
        self.0
            .retain(|_, (_, _, at, _)| at.elapsed() < Duration::from_secs(300));
        history.iter().cloned().map(|mut item| {
            if item["type"] != "function_call_output" { return item; }
            let Some(text) = item["output"].as_str() else { return item; };
            let Ok(value) = serde_json::from_str::<Value>(text) else { return item; };
            let Some(id) = value["ephemeral_image"]["id"].as_str() else { return item; };
            if let Some((_, image, _, _)) = self.0.get(sid).filter(|(i,_,_,_)|i==id) {
                item["output"] = json!([
                    {"type":"input_text","text":text},
                    {"type":"input_image","image_url":format!("data:{};base64,{}",image.mime_type,image.base64),"detail":"high"}
                ]);
            } else {
                item["output"] = json!(format!("{text}\nScreenshot pixels are no longer available in this request: only the latest screenshot is retained, and images expire. This notice does not mean capture failed or that pixels were absent from earlier requests. Use observations you recorded at the time for historical comparisons; observe again for the current view. Do not invent details that you did not observe."));
            }
            item
        }).collect()
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn image() -> ToolImage {
        ToolImage {
            mime_type: "image/jpeg".into(),
            base64: STANDARD.encode([255, 216, 255, 217]),
        }
    }
    #[test]
    fn pixels_are_ephemeral_scoped_and_latest_only() {
        let mut cache = Images::default();
        let meta = cache.put("s1", "test.image", vec![image()]).unwrap();
        let original = vec![
            json!({"type":"function_call_output","call_id":"c","output":json!({"ephemeral_image":meta}).to_string()}),
        ];
        assert!(!original[0].to_string().contains("base64"));
        assert!(cache.hydrate("s1", &original)[0]["output"].is_array());
        assert!(cache.hydrate("s2", &original)[0]["output"]
            .as_str()
            .unwrap()
            .contains("no longer"));
        cache.put("s1", "test.image", vec![image()]).unwrap();
        assert!(cache.hydrate("s1", &original)[0]["output"].is_string());
        cache.clear("s1");
        assert!(cache.0.is_empty());
        assert!(cache
            .put(
                "s1",
                "test.image",
                vec![ToolImage {
                    mime_type: "text/html".into(),
                    base64: "".into()
                }]
            )
            .is_err());
    }
}
