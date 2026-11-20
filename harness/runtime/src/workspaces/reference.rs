//! Durable reference provider for qualification. Project files are virtual SQLite
//! rows, so model-supplied paths are never resolved against the host filesystem.
use super::*;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use std::{
    path::{Path, PathBuf},
    time::Duration,
};

#[derive(Clone)]
pub struct ReferenceProvider(PathBuf);

#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Options {
    pod_profile: Option<String>,
}

impl Options {
    fn parse(options: ProviderOptions) -> Result<Self> {
        let o: Self = options.decode()?;
        ensure!(
            o.pod_profile
                .as_ref()
                .is_none_or(|p| matches!(p.as_str(), "small" | "medium")),
            WorkspaceError::Invalid
        );
        Ok(o)
    }
}
impl ReferenceProvider {
    /// The operator supplies a separate trusted database path per registration.
    pub fn open(path: &Path) -> Result<Self> {
        let db = Connection::open(path)?;
        db.execute_batch("PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS ws_data(id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS ws_keys(key TEXT PRIMARY KEY,data TEXT NOT NULL REFERENCES ws_data ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS ws_compute(id TEXT PRIMARY KEY,key TEXT UNIQUE NOT NULL REFERENCES ws_keys,profile TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ws_sessions(workspace TEXT NOT NULL,session TEXT NOT NULL,active INTEGER NOT NULL,PRIMARY KEY(workspace,session));
            CREATE TABLE IF NOT EXISTS ws_files(data TEXT NOT NULL REFERENCES ws_data ON DELETE CASCADE,path TEXT NOT NULL,content TEXT NOT NULL,PRIMARY KEY(data,path));")?;
        Ok(Self(path.to_owned()))
    }
    async fn transaction<T: Send + 'static>(
        &self,
        f: impl FnOnce(&rusqlite::Transaction<'_>) -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let path = self.0.clone();
        tokio::task::spawn_blocking(move || {
            let mut db =
                Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE)?;
            db.busy_timeout(Duration::from_secs(5))?;
            db.pragma_update(None, "foreign_keys", true)?;
            let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let value = f(&tx)?;
            tx.commit()?;
            Ok(value)
        })
        .await?
    }
}
struct Client {
    provider: ReferenceProvider,
    profile: String,
}
impl WorkspaceProvider for ReferenceProvider {
    fn start(&self, options: ProviderOptions) -> ProviderFuture<'_, Arc<dyn WorkspaceClient>> {
        Box::pin(async move {
            let profile = Options::parse(options)?
                .pod_profile
                .unwrap_or_else(|| "small".into());
            Ok(Arc::new(Client {
                provider: self.clone(),
                profile,
            }) as Arc<dyn WorkspaceClient>)
        })
    }
    fn detach(&self, workspace: String, session: String) -> ProviderFuture<'_, ()> {
        Box::pin(async move {
            self.start(Default::default())
                .await?
                .detach_session(WorkspaceAttachment {
                    workspace_id: workspace,
                    session_id: session,
                })
                .await
        })
    }
    fn recover(&self, id: String) -> ProviderFuture<'_, Arc<dyn WorkspaceClient>> {
        Box::pin(async move {
            let profile = self
                .transaction(move |tx| {
                    tx.query_row("SELECT profile FROM ws_compute WHERE id=?1", [id], |r| {
                        r.get(0)
                    })
                    .optional()?
                    .context(WorkspaceError::Missing)
                })
                .await?;
            Ok(Arc::new(Client {
                provider: self.clone(),
                profile,
            }) as Arc<dyn WorkspaceClient>)
        })
    }
}

impl WorkspaceClient for Client {
    fn get_or_create(&self, req: GetOrCreateWorkspace) -> ProviderFuture<'_, WorkspaceRef> {
        Box::pin(async move {
            let profile = Options::parse(req.options)?
                .pod_profile
                .unwrap_or_else(|| self.profile.clone());
            self.provider
                .transaction(move |tx| {
                    let bound: Option<String> = tx
                        .query_row("SELECT data FROM ws_keys WHERE key=?1", [&req.key], |r| {
                            r.get(0)
                        })
                        .optional()?;
                    let data = match (bound, req.data_id) {
                        (Some(data), requested) => {
                            ensure!(
                                requested.is_none_or(|d| d == data),
                                WorkspaceError::DataMismatch
                            );
                            data
                        }
                        (None, requested) => {
                            let data = if let Some(data) = requested {
                                ensure!(
                                    tx.prepare("SELECT 1 FROM ws_data WHERE id=?1")?
                                        .exists([&data])?,
                                    WorkspaceError::Missing
                                );
                                data
                            } else {
                                let id = crate::id("data");
                                tx.execute("INSERT INTO ws_data VALUES(?1)", [&id])?;
                                id
                            };
                            tx.execute(
                                "INSERT INTO ws_keys VALUES(?1,?2)",
                                params![req.key, data],
                            )?;
                            data
                        }
                    };
                    let existing: Option<(String, String)> = tx
                        .query_row(
                            "SELECT id,profile FROM ws_compute WHERE key=?1",
                            [&req.key],
                            |r| Ok((r.get(0)?, r.get(1)?)),
                        )
                        .optional()?;
                    let workspace_id = if let Some((id, old_profile)) = existing {
                        ensure!(old_profile == profile, WorkspaceError::ProfileMismatch);
                        id
                    } else {
                        let id = crate::id("workspace");
                        tx.execute(
                            "INSERT INTO ws_compute VALUES(?1,?2,?3)",
                            params![id, req.key, profile],
                        )?;
                        id
                    };
                    Ok(WorkspaceRef {
                        previews: Vec::new(),
                        workspace_id,
                        data_id: data,
                    })
                })
                .await
        })
    }
    fn attach_session(
        &self,
        workspace: String,
        session: String,
    ) -> ProviderFuture<'_, WorkspaceAttachment> {
        Box::pin(self.provider.transaction(move |tx| {
            ensure!(
                tx.prepare("SELECT 1 FROM ws_compute WHERE id=?1")?
                    .exists([&workspace])?,
                WorkspaceError::Missing
            );
            tx.execute(
                "INSERT OR IGNORE INTO ws_sessions VALUES(?1,?2,1)",
                params![workspace, session],
            )?;
            ensure!(
                tx.prepare(
                    "SELECT 1 FROM ws_sessions WHERE workspace=?1 AND session=?2 AND active=1"
                )?
                .exists(params![workspace, session])?,
                "Workspace attachment closed"
            );
            Ok(WorkspaceAttachment {
                workspace_id: workspace,
                session_id: session,
            })
        }))
    }
    fn detach_session(&self, attachment: WorkspaceAttachment) -> ProviderFuture<'_, ()> {
        let WorkspaceAttachment {
            workspace_id: workspace,
            session_id: session,
        } = attachment;
        Box::pin(self.provider.transaction(move |tx| {
            // A tombstone fences a delayed attach for a session already closed.
            tx.execute(
                "INSERT INTO ws_sessions(workspace, session, active)
                 SELECT ?1, ?2, 0 WHERE EXISTS(SELECT 1 FROM ws_compute WHERE id=?1)
                 ON CONFLICT(workspace,session) DO UPDATE SET active=0",
                params![workspace, session],
            )?;
            Ok(())
        }))
    }
    fn stop_workspace(&self, workspace: String) -> ProviderFuture<'_, ()> {
        Box::pin(self.provider.transaction(move |tx| {
            ensure!(
                !tx.prepare("SELECT 1 FROM ws_sessions WHERE workspace=?1 AND active=1")?
                    .exists([&workspace])?,
                WorkspaceError::Busy
            );
            tx.execute("DELETE FROM ws_compute WHERE id=?1", [&workspace])?;
            tx.execute("DELETE FROM ws_sessions WHERE workspace=?1", [workspace])?;
            Ok(())
        }))
    }
    fn delete_data(&self, data: String) -> ProviderFuture<'_, ()> {
        Box::pin(self.provider.transaction(move |tx| {
            ensure!(
                !tx.prepare(
                    "SELECT 1 FROM ws_compute c JOIN ws_keys k ON k.key=c.key WHERE k.data=?1"
                )?
                .exists([&data])?,
                WorkspaceError::Busy
            );
            tx.execute("DELETE FROM ws_data WHERE id=?1", [data])?;
            Ok(())
        }))
    }
    fn file(
        &self,
        attachment: WorkspaceAttachment,
        op: FileOperation,
    ) -> ProviderFuture<'_, Value> {
        let WorkspaceAttachment {
            workspace_id: workspace,
            session_id: session,
        } = attachment;
        Box::pin(self.provider.transaction(move |tx| {
            let data: String = tx
                .query_row(
                    "SELECT k.data FROM ws_sessions a
                 JOIN ws_compute c ON c.id=a.workspace JOIN ws_keys k ON k.key=c.key
                 WHERE a.workspace=?1 AND a.session=?2 AND a.active=1",
                    params![workspace, session],
                    |row| row.get(0),
                )
                .optional()?
                .context("Workspace attachment unavailable")?;
            let read = |path: &str| -> Result<String> {
                tx.query_row(
                    "SELECT content FROM ws_files WHERE data=?1 AND path=?2",
                    params![data, path],
                    |row| row.get(0),
                )
                .optional()?
                .context("File unavailable")
            };
            match op {
                FileOperation::ListFiles {} => {
                    let mut stmt = tx.prepare(
                        "SELECT path FROM ws_files WHERE data=?1 ORDER BY path LIMIT 1001",
                    )?;
                    let files = stmt
                        .query_map([&data], |row| row.get::<_, String>(0))?
                        .collect::<rusqlite::Result<Vec<_>>>()?;
                    ensure!(files.len() <= 1000, "File list exceeds limit");
                    Ok(json!({"files":files}))
                }
                FileOperation::ReadFile { path } => {
                    validate_path(&path)?;
                    Ok(json!({"path":path,"text":read(&path)?}))
                }
                FileOperation::WriteFile { path, content } => {
                    validate_path(&path)?;
                    ensure!(content.len() <= 128000, "File exceeds limit");
                    tx.execute(
                        "INSERT INTO ws_files VALUES(?1,?2,?3) ON CONFLICT(data,path)
                         DO UPDATE SET content=excluded.content",
                        params![data, path, content],
                    )?;
                    Ok(json!({"written":true}))
                }
                FileOperation::EditFile {
                    path,
                    old_text,
                    new_text,
                } => {
                    validate_path(&path)?;
                    let original = read(&path)?;
                    ensure!(
                        !old_text.is_empty() && original.matches(&old_text).count() == 1,
                        "Edit requires one exact match"
                    );
                    let updated = original.replacen(&old_text, &new_text, 1);
                    ensure!(updated.len() <= 128000, "File exceeds limit");
                    tx.execute(
                        "UPDATE ws_files SET content=?3 WHERE data=?1 AND path=?2",
                        params![data, path, updated],
                    )?;
                    Ok(json!({"edited":true}))
                }
            }
        }))
    }
}

fn validate_path(path: &str) -> Result<()> {
    ensure!(
        !path.contains(['\\', ':'])
            && !path.chars().any(char::is_control)
            && path.len() <= 512
            && path.split('/').all(|part| !matches!(part, "" | "." | "..")),
        "Expected workspace-relative path"
    );
    Ok(())
}
