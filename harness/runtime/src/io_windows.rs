//! Parent-owned anonymous pipes. No Tokio blocking-stdin task can hold shutdown.
use std::{
    fs::File,
    io::{Read, Write},
    os::windows::io::{AsRawHandle, FromRawHandle},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use windows_sys::Win32::{
    Foundation::*,
    Storage::FileSystem::{GetFileType, FILE_TYPE_PIPE},
    System::{
        Console::*, Pipes::PeekNamedPipe, Threading::GetCurrentProcess, IO::CancelSynchronousIo,
    },
};
pub struct Fd(File);
impl Fd {
    pub fn new(fd: i32) -> std::io::Result<Self> {
        unsafe {
            let source = GetStdHandle(if fd == 0 {
                STD_INPUT_HANDLE
            } else {
                STD_OUTPUT_HANDLE
            });
            if GetFileType(source) != FILE_TYPE_PIPE {
                return Err(std::io::Error::other(
                    "serve --stdio requires parent-owned pipes on Windows",
                ));
            }
            let mut h = std::ptr::null_mut();
            if DuplicateHandle(
                GetCurrentProcess(),
                source,
                GetCurrentProcess(),
                &mut h,
                0,
                0,
                DUPLICATE_SAME_ACCESS,
            ) == 0
            {
                return Err(std::io::Error::last_os_error());
            }
            Ok(Self(File::from_raw_handle(h)))
        }
    }
    pub async fn read(&self, max: usize) -> std::io::Result<Vec<u8>> {
        loop {
            let mut available = 0;
            if unsafe {
                PeekNamedPipe(
                    self.0.as_raw_handle(),
                    std::ptr::null_mut(),
                    0,
                    std::ptr::null_mut(),
                    &mut available,
                    std::ptr::null_mut(),
                )
            } == 0
            {
                let e = std::io::Error::last_os_error();
                if e.raw_os_error() == Some(ERROR_BROKEN_PIPE as i32) {
                    return Ok(vec![]);
                }
                return Err(e);
            }
            if available > 0 {
                let mut data = vec![0; max.min(available as usize)];
                let n = (&self.0).read(&mut data)?;
                data.truncate(n);
                return Ok(data);
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }
    pub async fn write_all(&self, bytes: &[u8]) -> std::io::Result<()> {
        let mut file = self.0.try_clone()?;
        let data = bytes.to_vec();
        let cancelled = Arc::new(AtomicBool::new(false));
        let check = cancelled.clone();
        let (tx, rx) = tokio::sync::oneshot::channel();
        let thread = std::thread::spawn(move || {
            if check.load(Ordering::Acquire) {
                return;
            }
            let result = file.write_all(&data);
            let _ = tx.send(result);
        });
        let guard = WriteGuard {
            thread: Some(thread),
            cancelled,
        };
        let result = rx
            .await
            .map_err(|_| std::io::Error::other("Pipe writer stopped"))?;
        drop(guard);
        result
    }
}
struct WriteGuard {
    thread: Option<std::thread::JoinHandle<()>>,
    cancelled: Arc<AtomicBool>,
}
impl Drop for WriteGuard {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            // Repeat through the small start/write race. This is a pipe-I/O worker,
            // not detached tool execution; it never owns approval or agent state.
            let end = std::time::Instant::now() + Duration::from_secs(2);
            while !thread.is_finished() && std::time::Instant::now() < end {
                unsafe {
                    CancelSynchronousIo(thread.as_raw_handle());
                }
                std::thread::sleep(Duration::from_millis(1));
            }
            if thread.is_finished() {
                let _ = thread.join();
            }
        }
    }
}
