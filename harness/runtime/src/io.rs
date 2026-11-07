//! Pollable Unix stdio: no uncancellable blocking stdin/stdout worker on exit.
use std::{
    fs::File,
    io::{Read, Write},
    os::fd::{AsRawFd, FromRawFd},
};
use tokio::io::unix::AsyncFd;

struct Owned {
    file: File,
    flags: i32,
}
impl AsRawFd for Owned {
    fn as_raw_fd(&self) -> i32 {
        self.file.as_raw_fd()
    }
}
impl Drop for Owned {
    fn drop(&mut self) {
        unsafe {
            libc::fcntl(self.file.as_raw_fd(), libc::F_SETFL, self.flags);
        }
    }
}
pub struct Fd(AsyncFd<Owned>);
impl Fd {
    pub fn new(fd: i32) -> std::io::Result<Self> {
        let copy = unsafe { libc::dup(fd) };
        if copy < 0 {
            return Err(std::io::Error::last_os_error());
        }
        let file = unsafe { File::from_raw_fd(copy) };
        let flags = unsafe { libc::fcntl(copy, libc::F_GETFL) };
        if flags < 0 || unsafe { libc::fcntl(copy, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: Owned keeps this File open with an invariant descriptor until
        // AsyncFd drops it; no API exposes ownership or replaces the File.
        Ok(Self(unsafe { AsyncFd::register(Owned { file, flags }) }?))
    }
    pub async fn read(&self, max: usize) -> std::io::Result<Vec<u8>> {
        loop {
            let mut ready = self.0.readable().await?;
            let mut bytes = vec![0; max];
            match ready.try_io(|fd| (&fd.get_ref().file).read(&mut bytes)) {
                Ok(Ok(n)) => {
                    bytes.truncate(n);
                    return Ok(bytes);
                }
                Ok(Err(e)) => return Err(e),
                Err(_) => {}
            }
        }
    }
    pub async fn write_all(&self, mut bytes: &[u8]) -> std::io::Result<()> {
        while !bytes.is_empty() {
            let mut ready = self.0.writable().await?;
            match ready.try_io(|fd| (&fd.get_ref().file).write(bytes)) {
                Ok(Ok(0)) => return Err(std::io::ErrorKind::WriteZero.into()),
                Ok(Ok(n)) => bytes = &bytes[n..],
                Ok(Err(e)) => return Err(e),
                Err(_) => {}
            }
        }
        Ok(())
    }
}
