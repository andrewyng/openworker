//! Small, immutable UTF-8 input files restored by the trusted replay controller.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

pub const MAX_FILES: usize = 16;
pub const MAX_TEXT_BYTES: usize = 16_384;
const MAX_WIRE_BYTES: usize = 48_000;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct File {
    pub path: String,
    pub text: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct Fixture(pub Vec<File>);

#[derive(Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub digest: String,
    pub files: usize,
}

impl Fixture {
    pub fn normalize(&mut self) -> Result<()> {
        ensure!(
            self.0.len() <= MAX_FILES,
            "A replay fixture supports at most {MAX_FILES} files"
        );
        ensure!(
            self.0.iter().map(|file| file.text.len()).sum::<usize>() <= MAX_TEXT_BYTES
                && serde_json::to_vec(self)?.len() <= MAX_WIRE_BYTES,
            "Replay fixture is too large"
        );
        let mut files = BTreeSet::new();
        let mut directories = BTreeMap::new();
        for file in &self.0 {
            ensure!(
                !file.path.is_empty() && file.path.len() <= 200,
                "Invalid fixture path length"
            );
            let parts: Vec<_> = file.path.split('/').collect();
            ensure!(parts.len() <= 8, "Fixture path is too deeply nested");
            ensure!(
                !file.text.contains('\0'),
                "Fixtures accept UTF-8 text files"
            );
            for part in &parts {
                let lower = part.to_ascii_lowercase();
                ensure!(
                    !part.is_empty()
                        && part.len() <= 80
                        && part
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
                        && !matches!(*part, "." | "..")
                        && lower != ".git"
                        && !lower.starts_with(".openworker")
                        && !lower.starts_with(".ow-fixture-"),
                    "Invalid or reserved fixture path component"
                );
            }
            let path = file.path.to_ascii_lowercase();
            ensure!(files.insert(path), "Duplicate fixture file path");
            for end in 1..parts.len() {
                let directory = parts[..end].join("/");
                let first = directories
                    .entry(directory.to_ascii_lowercase())
                    .or_insert_with(|| directory.clone());
                ensure!(
                    first == &directory,
                    "Fixture directory casing is inconsistent"
                );
            }
        }
        ensure!(
            !files.iter().any(|file| directories.contains_key(file)),
            "A fixture path is both a file and a directory"
        );
        self.0.sort_by(|a, b| a.path.cmp(&b.path));
        Ok(())
    }

    pub fn receipt(&self) -> Result<Receipt> {
        let mut fixture = self.clone();
        fixture.normalize()?;
        Ok(Receipt {
            digest: crate::bundle::digest(&serde_json::to_vec(&fixture)?),
            files: fixture.0.len(),
        })
    }
}

#[cfg(unix)]
mod restore {
    use super::*;
    use std::{
        ffi::{CStr, CString},
        fs::{File as DiskFile, OpenOptions},
        io::{self, Read, Write},
        os::{
            fd::{AsRawFd, FromRawFd},
            unix::fs::OpenOptionsExt,
        },
    };

    fn open(parent: &DiskFile, name: &CStr, flags: i32, mode: u32) -> io::Result<DiskFile> {
        // O_NOFOLLOW applies at every directory and file lookup. Holding the
        // parent descriptor also avoids reopening a swapped ancestor by path.
        let fd = unsafe {
            libc::openat(
                parent.as_raw_fd(),
                name.as_ptr(),
                flags | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                mode,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(unsafe { DiskFile::from_raw_fd(fd) })
    }

    fn directory(parent: &DiskFile, name: &CStr) -> io::Result<DiskFile> {
        if unsafe { libc::mkdirat(parent.as_raw_fd(), name.as_ptr(), 0o700) } < 0 {
            let error = io::Error::last_os_error();
            if error.kind() != io::ErrorKind::AlreadyExists {
                return Err(error);
            }
        }
        open(parent, name, libc::O_RDONLY | libc::O_DIRECTORY, 0)
    }

    fn matching(parent: &DiskFile, name: &CStr, expected: &[u8]) -> Result<bool> {
        match open(parent, name, libc::O_RDONLY | libc::O_NONBLOCK, 0) {
            Ok(file) => {
                let stat = file.metadata()?;
                ensure!(
                    stat.is_file() && stat.len() == expected.len() as u64,
                    "Existing fixture file differs"
                );
                let mut bytes = Vec::with_capacity(expected.len());
                file.take(expected.len() as u64 + 1)
                    .read_to_end(&mut bytes)?;
                ensure!(bytes == expected, "Existing fixture file differs");
                Ok(true)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(error.into()),
        }
    }

    fn publish(parent: &DiskFile, target: &CStr, contents: &[u8]) -> Result<()> {
        if matching(parent, target, contents)? {
            return Ok(());
        }
        let temp = CString::new(crate::id(".ow-fixture-"))?;
        let fd = parent.as_raw_fd();
        let mut file = open(
            parent,
            &temp,
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
            0o600,
        )?;
        let result = (|| {
            file.write_all(contents)?;
            file.sync_all()?;
            if unsafe { libc::linkat(fd, temp.as_ptr(), fd, target.as_ptr(), 0) } < 0 {
                let error = io::Error::last_os_error();
                if error.kind() != io::ErrorKind::AlreadyExists {
                    return Err(error.into());
                }
                ensure!(
                    matching(parent, target, contents)?,
                    "Fixture publication raced with removal"
                );
            }
            Ok(())
        })();
        unsafe { libc::unlinkat(fd, temp.as_ptr(), 0) };
        result
    }

    pub(super) fn apply(root: &Path, mut fixture: Fixture, verify: bool) -> Result<Receipt> {
        fixture.normalize()?;
        let root = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
            .open(root)?;
        for file in &fixture.0 {
            let mut parent = root.try_clone()?;
            let (parents, name) = file.path.rsplit_once('/').unwrap_or(("", &file.path));
            if !parents.is_empty() {
                for part in parents.split('/') {
                    let part = CString::new(part)?;
                    parent = if verify {
                        open(&parent, &part, libc::O_RDONLY | libc::O_DIRECTORY, 0)?
                    } else {
                        directory(&parent, &part)?
                    };
                }
            }
            let target = CString::new(name)?;
            if verify {
                ensure!(
                    matching(&parent, &target, file.text.as_bytes())?,
                    "Fixture file is absent"
                );
            } else {
                publish(&parent, &target, file.text.as_bytes())?;
                parent.sync_all()?;
            }
        }
        fixture.receipt()
    }
}

pub fn restore(root: &Path, fixture: Fixture, verify: bool) -> Result<Receipt> {
    #[cfg(unix)]
    return restore::apply(root, fixture, verify);
    #[cfg(not(unix))]
    {
        let _ = (root, fixture, verify);
        anyhow::bail!("Replay workspace fixtures require a Unix sandbox runner")
    }
}
