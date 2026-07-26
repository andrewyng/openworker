use serde::Serialize;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

#[derive(Clone, Serialize)]
pub struct TtsStatus {
    pub is_playing: bool,
    pub model_installed: bool,
    pub download_in_progress: bool,
    pub model_name: &'static str,
    pub model_bytes: u64,
}

#[derive(Clone, Serialize)]
pub struct DownloadProgress {
    pub bytes_downloaded: u64,
    pub total_bytes: u64,
}

pub struct TtsEngine {
    models_dir: PathBuf,
    is_playing: Arc<Mutex<bool>>,
    model_installed: Arc<Mutex<bool>>,
    download_in_progress: Arc<Mutex<bool>>,
}

impl TtsEngine {
    pub fn new(models_dir: PathBuf) -> Self {
        // Check if model file exists
        let model_path = models_dir.join("piper_model.onnx");
        let installed = model_path.exists();

        Self {
            models_dir,
            is_playing: Arc::new(Mutex::new(false)),
            model_installed: Arc::new(Mutex::new(installed)),
            download_in_progress: Arc::new(Mutex::new(false)),
        }
    }

    pub fn status(&self) -> TtsStatus {
        TtsStatus {
            is_playing: *self.is_playing.lock().unwrap(),
            model_installed: *self.model_installed.lock().unwrap(),
            download_in_progress: *self.download_in_progress.lock().unwrap(),
            model_name: "en_US-lessac-medium",
            model_bytes: 45_000_000,
        }
    }

    pub fn install_default_model_with_progress<F>(&self, mut progress_callback: F) -> Result<(), String>
    where
        F: FnMut(DownloadProgress) + Send + 'static,
    {
        *self.download_in_progress.lock().unwrap() = true;
        
        // Simulate a download
        let total = 45_000_000;
        let mut downloaded = 0;
        while downloaded < total {
            thread::sleep(Duration::from_millis(100));
            downloaded += 5_000_000;
            if downloaded > total {
                downloaded = total;
            }
            progress_callback(DownloadProgress {
                bytes_downloaded: downloaded,
                total_bytes: total,
            });
        }

        std::fs::create_dir_all(&self.models_dir).map_err(|e| e.to_string())?;
        std::fs::write(self.models_dir.join("piper_model.onnx"), b"dummy model data").map_err(|e| e.to_string())?;

        *self.model_installed.lock().unwrap() = true;
        *self.download_in_progress.lock().unwrap() = false;
        Ok(())
    }

    pub fn synthesize_and_play(&self, text: &str) -> Result<(), String> {
        if !*self.model_installed.lock().unwrap() {
            return Err("Voice model not installed".into());
        }

        *self.is_playing.lock().unwrap() = true;
        
        // TODO: Integrate actual ONNX Piper model inference and rodio playback here.
        // For now, we simulate playback delay based on text length.
        let delay_ms = text.len() as u64 * 50; 
        
        // Optional: Call OS native TTS to actually hear it during development
        #[cfg(target_os = "macos")]
        {
            let _ = std::process::Command::new("say").arg(text).status();
        }
        #[cfg(not(target_os = "macos"))]
        {
            thread::sleep(Duration::from_millis(delay_ms));
        }

        *self.is_playing.lock().unwrap() = false;
        Ok(())
    }

    pub fn stop(&self) {
        *self.is_playing.lock().unwrap() = false;
        // In a real implementation, this would signal the rodio Sink to stop.
        #[cfg(target_os = "macos")]
        {
            // Kill 'say' processes if any
            let _ = std::process::Command::new("killall").arg("say").status();
        }
    }

    pub fn delete_default_model(&self) -> Result<(), String> {
        let model_path = self.models_dir.join("piper_model.onnx");
        if model_path.exists() {
            std::fs::remove_file(model_path).map_err(|e| e.to_string())?;
        }
        *self.model_installed.lock().unwrap() = false;
        Ok(())
    }
}
