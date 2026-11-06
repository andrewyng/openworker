use crate::*;
use cap_std::fs::{Dir, OpenOptions};
use std::{
    io::{Read, Write},
    path::Path,
};

pub struct Filesystem {
    root: Arc<Dir>,
}
fn map(error: std::io::Error) -> anyhow::Error {
    match error.kind() {
        std::io::ErrorKind::NotFound => Error::NotFound.into(),
        std::io::ErrorKind::AlreadyExists => Error::Conflict.into(),
        _ => Error::Unavailable.into(),
    }
}
fn read(root: &Dir, name: &str, limit: usize) -> Result<Bytes> {
    // Capability-relative open cannot traverse outside root even if the path
    // changes during access. Hash-derived names contain no user path segments.
    let metadata = root.symlink_metadata(name).map_err(map)?;
    ensure!(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        Error::Conflict
    );
    let file = root.open(name).map_err(map)?;
    ensure!(file.metadata().map_err(map)?.is_file(), Error::Conflict);
    let mut bytes = vec![];
    file.take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(map)?;
    ensure!(bytes.len() <= limit, Error::Conflict);
    Ok(bytes.into())
}
fn create(root: &Dir, name: &str, bytes: &[u8]) -> Result<()> {
    let temp = format!("stage-{}", uuid::Uuid::new_v4().simple());
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut f = root.open_with(&temp, &options).map_err(map)?;
    let result = (|| {
        f.write_all(bytes).map_err(map)?;
        f.sync_all().map_err(map)?;
        let linked = root.hard_link(&temp, root, name);
        if let Err(e) = &linked {
            ensure!(
                e.kind() == std::io::ErrorKind::AlreadyExists,
                Error::Unavailable
            );
        }
        #[cfg(unix)]
        // A capability Dir may hold an O_PATH descriptor on Linux, which
        // cannot be fsynced. Reopen '.' read-only relative to that capability.
        root.open(".").map_err(map)?.sync_all().map_err(map)?;
        // Sync even on an existing object: its previous creator may have lost
        // the directory-sync result. Only then allow identical-byte recovery.
        linked.map_err(map)
    })();
    drop(f);
    let _ = root.remove_file(&temp);
    result
}
impl Filesystem {
    pub fn open(
        path: &Path,
        deployment: &str,
        shared_durable: bool,
        multi_node: bool,
    ) -> Result<Store> {
        component(deployment, 64)?;
        ensure!(!multi_node || shared_durable, Error::Invalid);
        ensure!(
            !std::fs::symlink_metadata(path)
                .map_err(map)?
                .file_type()
                .is_symlink(),
            Error::Invalid
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            ensure!(
                std::fs::metadata(path).map_err(map)?.permissions().mode() & 0o022 == 0,
                Error::Invalid
            );
        }
        let root = Dir::open_ambient_dir(path, cap_std::ambient_authority()).map_err(map)?;
        let fresh = uuid::Uuid::new_v4().to_string();
        match create(&root, "store-identity", fresh.as_bytes()) {
            Ok(()) => {}
            Err(e) if e.downcast_ref::<Error>() == Some(&Error::Conflict) => {}
            Err(e) => return Err(e),
        }
        let id = read(&root, "store-identity", 36)?;
        let id = std::str::from_utf8(&id).map_err(|_| Error::Conflict)?;
        uuid::Uuid::parse_str(id).map_err(|_| Error::Conflict)?;
        Store::new(
            Arc::new(Self {
                root: Arc::new(root),
            }),
            Binding {
                deployment: deployment.into(),
                identity: hash(format!("filesystem-v1:{id}:{deployment}").as_bytes()),
            },
        )
    }
    pub fn object_filename(key: &str) -> String {
        format!("object-{}", hash(key.as_bytes()))
    }
}
#[async_trait::async_trait]
impl Backend for Filesystem {
    async fn create(&self, key: &str, bytes: Bytes) -> Result<()> {
        ensure!(bytes.len() <= CHUNK, Error::TooLarge);
        let name = Self::object_filename(key);
        let root = self.root.clone();
        tokio::task::spawn_blocking(move || create(&root, &name, &bytes))
            .await
            .map_err(|_| Error::Unavailable)?
    }
    async fn read(&self, key: &str, limit: usize) -> Result<Bytes> {
        ensure!(limit <= CHUNK, Error::TooLarge);
        let name = Self::object_filename(key);
        let root = self.root.clone();
        tokio::task::spawn_blocking(move || read(&root, &name, limit))
            .await
            .map_err(|_| Error::Unavailable)?
    }
}
