//! Descriptor-relative file updates for a trusted workspace approval scope.
use anyhow::{ensure, Context, Result};
use std::{
    ffi::CString,
    fs::{File, OpenOptions},
    io::{self, Read, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{
            ffi::OsStrExt,
            fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
        },
    },
    path::Path,
};

pub(super) struct Target {
    directory: File,
    name: CString,
}

fn open_at(parent: &File, name: &CString, flags: libc::c_int) -> io::Result<File> {
    // SAFETY: the file descriptor and NUL-terminated name are valid for this call.
    let fd = unsafe {
        libc::openat(
            parent.as_raw_fd(),
            name.as_ptr(),
            flags | libc::O_CLOEXEC | libc::O_NOFOLLOW,
            0o600,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: openat returned a newly owned descriptor.
    Ok(unsafe { File::from_raw_fd(fd) })
}

fn missing(error: &io::Error) -> bool {
    error.kind() == io::ErrorKind::NotFound
}

impl Target {
    pub(super) fn open(
        root: &Path,
        path: &str,
        directory: &str,
        create_parents: bool,
    ) -> Result<Self> {
        ensure!(
            crate::permissions::under_workspace_directory(directory, path),
            "File is outside the approved directory"
        );
        let relative = crate::runner::relative_path(path)?;
        let mut directory = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
            .open(root)?;
        let mut components = relative.components().peekable();
        while let Some(component) = components.next() {
            let name = CString::new(component.as_os_str().as_bytes())?;
            if components.peek().is_none() {
                return Ok(Self { directory, name });
            }
            let flags = libc::O_RDONLY | libc::O_DIRECTORY;
            directory = match open_at(&directory, &name, flags) {
                Err(error) if create_parents && missing(&error) => {
                    // SAFETY: the opened parent and component name remain valid.
                    if unsafe { libc::mkdirat(directory.as_raw_fd(), name.as_ptr(), 0o700) } != 0 {
                        let error = io::Error::last_os_error();
                        if error.kind() != io::ErrorKind::AlreadyExists {
                            return Err(error.into());
                        }
                    }
                    open_at(&directory, &name, flags)?
                }
                child => child?,
            };
        }
        unreachable!("validated workspace paths have at least one component")
    }

    fn current(&self) -> Result<Option<File>> {
        match open_at(
            &self.directory,
            &self.name,
            libc::O_RDONLY | libc::O_NONBLOCK,
        ) {
            Ok(file) => {
                let meta = file.metadata()?;
                ensure!(
                    meta.is_file() && meta.nlink() == 1,
                    "Expected a regular file without links"
                );
                Ok(Some(file))
            }
            Err(error) if missing(&error) => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    pub(super) fn read(&self, limit: usize) -> Result<String> {
        let file = self.current()?.context("File does not exist")?;
        let mut bytes = Vec::new();
        file.take(limit as u64 + 1).read_to_end(&mut bytes)?;
        ensure!(bytes.len() <= limit, "File exceeds size limit");
        Ok(String::from_utf8(bytes)?)
    }

    pub(super) fn write(&self, content: &str, create_only: bool) -> Result<()> {
        let existing = self.current()?;
        ensure!(!create_only || existing.is_none(), "File already exists");
        let temporary = CString::new(format!(".ow-edit-{}", uuid::Uuid::new_v4()))?;
        let mut file = open_at(
            &self.directory,
            &temporary,
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
        )?;
        let fd = self.directory.as_raw_fd();
        let result: Result<()> = (|| {
            if let Some(existing) = existing {
                file.set_permissions(std::fs::Permissions::from_mode(
                    existing.metadata()?.mode() & 0o777,
                ))?;
            }
            file.write_all(content.as_bytes())?;
            file.sync_all()?;
            // SAFETY: both names are relative to the same retained descriptor.
            // rename/link operate on the entry itself and never traverse a target symlink.
            let rc = unsafe {
                if create_only {
                    libc::linkat(fd, temporary.as_ptr(), fd, self.name.as_ptr(), 0)
                } else {
                    libc::renameat(fd, temporary.as_ptr(), fd, self.name.as_ptr())
                }
            };
            if rc != 0 {
                return Err(io::Error::last_os_error().into());
            }
            Ok(())
        })();
        // SAFETY: the saved directory descriptor and temporary name are still valid.
        unsafe {
            libc::unlinkat(fd, temporary.as_ptr(), 0);
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use crate::coding::files::scoped_write;
    use serde_json::json;
    use std::{fs, os::unix::fs::symlink};

    #[tokio::test]
    async fn reviewed_file_runner_limits_operations_to_the_named_file() {
        let root = tempfile::tempdir().unwrap();
        let root = root.path().canonicalize().unwrap();
        let workspace = root.join("workspace");
        let allowed = root.join("allowed");
        fs::create_dir(&workspace).unwrap();
        fs::create_dir(&allowed).unwrap();
        let file = allowed.join("reviewed.txt");
        let mut tools = crate::coding::CodingTools::default();
        let written = tools
            .call(
                &workspace,
                "write_reviewed_file",
                &json!({
                    "path": file, "create_only": true, "content": "approved"
                }),
            )
            .await
            .unwrap();
        assert_eq!(written["path"], json!(file));
        let read = tools
            .call(&workspace, "read_reviewed_file", &json!({"path": file}))
            .await
            .unwrap();
        assert_eq!(read["lines"], json!(["approved"]));
        tools.call(&workspace, "edit_reviewed_file", &json!({
            "path": file, "old_text": "approved", "new_text": "complete", "expected_sha256": read["sha256"]
        })).await.unwrap();
        assert_eq!(fs::read_to_string(&file).unwrap(), "complete");
        symlink(&file, allowed.join("link")).unwrap();
        for path in [
            allowed.join("link"),
            allowed.join("missing/new.txt"),
            workspace.join(".openworker-coding.lock"),
        ] {
            assert!(tools
                .call(
                    &workspace,
                    "write_reviewed_file",
                    &json!({"path":path,"content":"denied"})
                )
                .await
                .is_err());
        }
        assert!(!allowed.join("missing").exists());
        assert_eq!(fs::read_to_string(&file).unwrap(), "complete");
    }

    #[test]
    fn scoped_mutations_refuse_sibling_paths_links_and_stale_content() {
        let root = tempfile::tempdir().unwrap();
        let workspace = root.path().join("workspace");
        let outside = root.path().join("outside");
        fs::create_dir_all(workspace.join("reports")).unwrap();
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("untouched"), "outside").unwrap();
        symlink(&outside, workspace.join("reports/link")).unwrap();
        for path in [
            "reports-old/new",
            "reports/link/untouched",
            "reports/../other",
        ] {
            assert!(scoped_write(
                &workspace,
                "write_file",
                &json!({"path":path,"content":"denied"}),
                "reports/"
            )
            .is_err());
        }
        let args = json!({"path":"reports/daily/report","content":"before","create_only":true});
        let written = scoped_write(&workspace, "write_file", &args, "reports/").unwrap();
        assert!(scoped_write(&workspace, "write_file", &args, "reports").is_err());
        let mut edit = json!({"path":args["path"],"old_text":"before","new_text":"after","expected_sha256":"stale"});
        assert!(scoped_write(&workspace, "edit_file", &edit, "reports").is_err());
        edit["expected_sha256"] = written["sha256"].clone();
        scoped_write(&workspace, "edit_file", &edit, "reports").unwrap();
        assert_eq!(
            fs::read_to_string(workspace.join("reports/daily/report")).unwrap(),
            "after"
        );
        assert_eq!(
            fs::read_to_string(outside.join("untouched")).unwrap(),
            "outside"
        );
        assert!(!workspace.join("reports-old").exists());
    }
}
