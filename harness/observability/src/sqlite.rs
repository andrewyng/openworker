use crate::{Conflict, Step, Store, StoredStep, Write};
use anyhow::{ensure, Context, Result};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use std::{path::Path, time::Duration};
pub struct Sqlite {
    db: Connection,
}
impl Sqlite {
    pub fn open(path: &Path) -> Result<Self> {
        // macOS exposes normal temporary directories through /var -> /private/var.
        // Resolve the parent only; never follow a symlink database leaf.
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let resolved = parent.canonicalize()?.join(
            path.file_name()
                .context("Missing observation database file name")?,
        );
        let path = resolved.as_path();
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        match options.open(path) {
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(e.into()),
        }
        ensure!(
            std::fs::symlink_metadata(path)?.file_type().is_file(),
            "Observation SQLite path must be a regular file"
        );
        let db = Connection::open_with_flags(
            path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX
                | OpenFlags::SQLITE_OPEN_NOFOLLOW,
        )?;
        db.busy_timeout(Duration::from_millis(500))?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS ow_observation_version(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL);")?;
        let version: Option<i64> = db
            .query_row(
                "SELECT version FROM ow_observation_version WHERE singleton=1",
                [],
                |r| r.get(0),
            )
            .optional()?;
        match version {
            Some(1) => {}
            Some(_) => anyhow::bail!("Unsupported SQLite observation schema"),
            None => {
                ensure!(
                    db.query_row(
                        "SELECT count(*) FROM sqlite_master WHERE name='ow_observation_steps'",
                        [],
                        |r| r.get::<_, i64>(0)
                    )? == 0,
                    "Refusing unversioned observation table"
                );
                db.execute_batch("BEGIN IMMEDIATE;
                  CREATE TABLE ow_observation_steps(cursor INTEGER PRIMARY KEY AUTOINCREMENT, deployment TEXT NOT NULL, span_id TEXT NOT NULL, session_hash TEXT, ended_ms INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE(deployment,span_id));
                  CREATE INDEX ow_observation_session ON ow_observation_steps(deployment,session_hash,cursor);
                  CREATE INDEX ow_observation_expiry ON ow_observation_steps(deployment,ended_ms,cursor);
                  INSERT INTO ow_observation_version VALUES(1,1); COMMIT;")?;
            }
        }
        db.prepare("SELECT cursor,deployment,span_id,session_hash,ended_ms,data FROM ow_observation_steps LIMIT 0")?;
        Ok(Self { db })
    }
}
impl Store for Sqlite {
    fn put(&mut self, step: &Step) -> Result<Write> {
        step.validate()?;
        let value = serde_json::to_string(step)?;
        let tx = self.db.transaction()?;
        let inserted=tx.execute("INSERT INTO ow_observation_steps(deployment,span_id,session_hash,ended_ms,data) VALUES(?,?,?,?,?) ON CONFLICT(deployment,span_id) DO NOTHING",params![step.deployment,step.span_id,step.session_hash,step.ended_ms as i64,value])?;
        if inserted == 0 {
            let existing: String = tx.query_row(
                "SELECT data FROM ow_observation_steps WHERE deployment=? AND span_id=?",
                params![step.deployment, step.span_id],
                |r| r.get(0),
            )?;
            if serde_json::from_str::<Step>(&existing)? != *step {
                return Err(Conflict.into());
            }
        }
        tx.commit()?;
        Ok(if inserted == 1 {
            Write::Inserted
        } else {
            Write::Duplicate
        })
    }
    fn get(&mut self, deployment: &str, span_id: &str) -> Result<Option<Step>> {
        crate::query(deployment, None, 0, 1)?;
        ensure!(crate::hex_id(span_id, 16), "Invalid observation identity");
        let s: Option<String> = self
            .db
            .query_row(
                "SELECT data FROM ow_observation_steps WHERE deployment=? AND span_id=?",
                params![deployment, span_id],
                |r| r.get(0),
            )
            .optional()?;
        s.map(|s| serde_json::from_str(&s).map_err(Into::into))
            .transpose()
    }
    fn list(
        &mut self,
        deployment: &str,
        session: Option<&str>,
        after: u64,
        limit: u16,
    ) -> Result<Vec<StoredStep>> {
        crate::query(deployment, session, after, limit)?;
        let mut q=self.db.prepare("SELECT cursor,data FROM ow_observation_steps WHERE deployment=? AND (? IS NULL OR session_hash=?) AND cursor>? ORDER BY cursor LIMIT ?")?;
        let rows = q.query_map(
            params![deployment, session, session, after as i64, i64::from(limit)],
            |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)),
        )?;
        rows.map(|r| {
            let (c, s) = r?;
            Ok(StoredStep {
                cursor: c as u64,
                step: serde_json::from_str(&s)?,
            })
        })
        .collect()
    }
    fn prune(&mut self, deployment: &str, cutoff: u64, limit: u16) -> Result<u64> {
        crate::query(deployment, None, 0, limit)?;
        ensure!(
            cutoff <= i64::MAX as u64 && (1..=1000).contains(&limit),
            "Invalid observation retention bound"
        );
        Ok(self.db.execute("DELETE FROM ow_observation_steps WHERE cursor IN (SELECT cursor FROM ow_observation_steps WHERE deployment=? AND ended_ms<? ORDER BY ended_ms,cursor LIMIT ?)",params![deployment,cutoff as i64,i64::from(limit)])? as u64)
    }
}
