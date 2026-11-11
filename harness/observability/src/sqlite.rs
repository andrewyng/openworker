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
        ensure!(db.query_row("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('sessions','events','submissions')", [], |r| r.get::<_, i64>(0))? == 0, "Recording database must be separate from execution storage");
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
        db.execute_batch("CREATE INDEX IF NOT EXISTS ow_observation_turn ON ow_observation_steps(deployment,session_hash,json_extract(data,'$.turn_hash'),cursor)")?;
        Ok(Self { db })
    }
}
impl Store for Sqlite {
    #[cfg(feature = "evaluations")]
    fn register_evaluation_sink(
        &mut self,
        deployment: &str,
        sink: &str,
        binding: &str,
    ) -> Result<()> {
        crate::evaluations::delivery_key(deployment, sink, binding, "registration")?;
        let tx = self
            .db
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute_batch("CREATE TABLE IF NOT EXISTS ow_evaluation_delivery(deployment TEXT NOT NULL,sink TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(deployment,sink))")?;
        tx.execute(
            "INSERT INTO ow_evaluation_delivery VALUES(?,?,?) ON CONFLICT DO NOTHING",
            params![
                deployment,
                sink,
                serde_json::to_string(&openworker_compliance::delivery::Checkpoint::new(
                    binding.into()
                ))?
            ],
        )?;
        let raw: String = tx.query_row(
            "SELECT data FROM ow_evaluation_delivery WHERE deployment=? AND sink=?",
            params![deployment, sink],
            |r| r.get(0),
        )?;
        let c: openworker_compliance::delivery::Checkpoint = serde_json::from_str(&raw)?;
        ensure!(
            c.binding == binding,
            "Evaluation destination changed; choose a new sink name"
        );
        tx.commit()?;
        Ok(())
    }

    #[cfg(feature = "evaluations")]
    fn prune_evaluations(&mut self, deployment: &str, before_ms: u64, limit: u16) -> Result<u64> {
        crate::query(deployment, None, 0, limit)?;
        ensure!(before_ms <= i64::MAX as u64, "Invalid cutoff");
        if self.db.query_row(
            "SELECT count(*) FROM sqlite_master WHERE name='ow_observation_evaluations'",
            [],
            |r| r.get::<_, i64>(0),
        )? == 0
        {
            return Ok(0);
        }
        self.db.execute_batch("CREATE TABLE IF NOT EXISTS ow_evaluation_delivery(deployment TEXT NOT NULL,sink TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(deployment,sink))")?;
        Ok(self.db.execute("DELETE FROM ow_observation_evaluations WHERE cursor IN(SELECT e.cursor FROM ow_observation_evaluations e WHERE e.deployment=?1 AND e.recorded_ms<?2 AND NOT EXISTS(SELECT 1 FROM ow_evaluation_delivery d WHERE d.deployment=e.deployment AND json_extract(d.data,'$.cursor')<e.cursor) ORDER BY e.cursor LIMIT ?3)",params![deployment,before_ms as i64,i64::from(limit)])? as u64)
    }

    #[cfg(feature = "evaluations")]
    fn claim_evaluation_export(
        &mut self,
        deployment: &str,
        sink: &str,
        binding: &str,
        token: &str,
        now: u64,
    ) -> Result<Option<crate::evaluations::Delivery>> {
        use openworker_compliance::delivery::Checkpoint;
        crate::evaluations::delivery_key(deployment, sink, binding, token)?;
        let tx = self
            .db
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute_batch("CREATE TABLE IF NOT EXISTS ow_evaluation_delivery(deployment TEXT NOT NULL,sink TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(deployment,sink))")?;
        let raw: Option<String> = tx
            .query_row(
                "SELECT data FROM ow_evaluation_delivery WHERE deployment=? AND sink=?",
                params![deployment, sink],
                |r| r.get(0),
            )
            .optional()?;
        let mut checkpoint = raw
            .map(|s| serde_json::from_str::<Checkpoint>(&s))
            .transpose()?
            .unwrap_or_else(|| Checkpoint::new(binding.into()));
        ensure!(
            checkpoint.binding == binding,
            "Export destination changed; choose a new sink name"
        );
        tx.execute(
            "INSERT INTO ow_evaluation_delivery VALUES(?,?,?) ON CONFLICT DO NOTHING",
            params![deployment, sink, serde_json::to_string(&checkpoint)?],
        )?;
        if tx.query_row(
            "SELECT count(*) FROM sqlite_master WHERE name='ow_observation_evaluations'",
            [],
            |r| r.get::<_, i64>(0),
        )? == 0
        {
            tx.commit()?;
            return Ok(None);
        }
        let row:Option<(i64,String)>=tx.query_row("SELECT cursor,data FROM ow_observation_evaluations WHERE deployment=? AND cursor>? ORDER BY cursor LIMIT 1",params![deployment,checkpoint.cursor],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
        let Some((cursor, data)) = row else {
            tx.commit()?;
            return Ok(None);
        };
        let claimed = checkpoint.claim(binding, token, now, cursor)?;
        let evaluation: crate::evaluations::Evaluation = serde_json::from_str(&data)?;
        evaluation.validate()?;
        ensure!(
            evaluation.deployment == deployment,
            "Evaluation export scope mismatch"
        );
        tx.execute(
            "UPDATE ow_evaluation_delivery SET data=? WHERE deployment=? AND sink=?",
            params![serde_json::to_string(&checkpoint)?, deployment, sink],
        )?;
        tx.commit()?;
        Ok(claimed.then(|| crate::evaluations::Delivery {
            token: token.into(),
            row: crate::evaluations::Row {
                cursor: cursor as u64,
                evaluation,
            },
        }))
    }
    #[cfg(feature = "evaluations")]
    fn settle_evaluation_export(
        &mut self,
        deployment: &str,
        sink: &str,
        token: &str,
        now: u64,
        success: bool,
    ) -> Result<()> {
        let tx = self
            .db
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let raw: String = tx.query_row(
            "SELECT data FROM ow_evaluation_delivery WHERE deployment=? AND sink=?",
            params![deployment, sink],
            |r| r.get(0),
        )?;
        let mut c: openworker_compliance::delivery::Checkpoint = serde_json::from_str(&raw)?;
        c.settle(token, now, success)?;
        tx.execute(
            "UPDATE ow_evaluation_delivery SET data=? WHERE deployment=? AND sink=?",
            params![serde_json::to_string(&c)?, deployment, sink],
        )?;
        tx.commit()?;
        Ok(())
    }

    #[cfg(feature = "evaluations")]
    fn evaluation(
        &mut self,
        deployment: &str,
        id: &str,
    ) -> Result<Option<crate::evaluations::Evaluation>> {
        ensure!(
            crate::identifier(deployment) && crate::label(id),
            "Invalid evaluation identity"
        );
        if self.db.query_row(
            "SELECT count(*) FROM sqlite_master WHERE name='ow_observation_evaluations'",
            [],
            |r| r.get::<_, i64>(0),
        )? == 0
        {
            return Ok(None);
        }
        let raw: Option<String> = self
            .db
            .query_row(
                "SELECT data FROM ow_observation_evaluations WHERE deployment=? AND id=?",
                params![deployment, id],
                |r| r.get(0),
            )
            .optional()?;
        raw.map(|raw| {
            let e: crate::evaluations::Evaluation = serde_json::from_str(&raw)?;
            e.validate()?;
            ensure!(
                e.deployment == deployment && e.result.id == id,
                "Evaluation identity mismatch"
            );
            Ok(e)
        })
        .transpose()
    }
    #[cfg(feature = "evaluations")]
    fn record_evaluation_bounded(
        &mut self,
        value: &crate::evaluations::Evaluation,
        max_pending: u32,
    ) -> Result<Write> {
        ensure!(
            (1..=1_000_000).contains(&max_pending),
            "Invalid evaluation export backlog limit"
        );
        use rusqlite::OptionalExtension;
        value.validate()?;
        let value = value.clone().redacted();
        value.validate()?;
        let data = serde_json::to_string(&value)?;
        // Existing corrections remain idempotent even after their predecessor expires.
        if let Some(old) = self.evaluation(&value.deployment, &value.result.id)? {
            ensure!(old == value, "Evaluation identity conflict");
            return Ok(Write::Duplicate);
        }
        let tx = self
            .db
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute_batch("CREATE TABLE IF NOT EXISTS ow_evaluation_delivery(deployment TEXT NOT NULL,sink TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(deployment,sink)); CREATE TABLE IF NOT EXISTS ow_observation_evaluations(cursor INTEGER PRIMARY KEY AUTOINCREMENT,deployment TEXT NOT NULL,id TEXT NOT NULL,session_hash TEXT NOT NULL,turn_hash TEXT NOT NULL,data TEXT NOT NULL,recorded_ms INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000),UNIQUE(deployment,id)); CREATE INDEX IF NOT EXISTS ow_evaluation_scope ON ow_observation_evaluations(deployment,session_hash,turn_hash,cursor)")?;
        let retained: i64 = tx.query_row(
            "SELECT count(*) FROM ow_observation_evaluations e WHERE deployment=? AND EXISTS(SELECT 1 FROM ow_evaluation_delivery d WHERE d.deployment=e.deployment AND json_extract(d.data,'$.cursor')<e.cursor)",
            [&value.deployment],
            |r| r.get(0),
        )?;
        let exists: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM ow_observation_evaluations WHERE deployment=? AND id=?)",
            params![value.deployment, value.result.id],
            |r| r.get(0),
        )?;
        ensure!(
            exists || retained < i64::from(max_pending),
            "Pending evaluation export limit reached; restore delivery or raise the configured limit"
        );
        if let Some(id) = &value.result.supersedes {
            let prior: Option<String> = tx
                .query_row(
                    "SELECT data FROM ow_observation_evaluations WHERE deployment=? AND id=?",
                    params![value.deployment, id],
                    |r| r.get(0),
                )
                .optional()?;
            let prior: crate::evaluations::Evaluation =
                serde_json::from_str(&prior.context("Superseded evaluation missing")?)?;
            ensure!(
                prior.target == value.target,
                "Correction must retain its target"
            );
        }
        let inserted=tx.execute("INSERT INTO ow_observation_evaluations(deployment,id,session_hash,turn_hash,data) VALUES(?,?,?,?,?) ON CONFLICT(deployment,id) DO NOTHING",params![value.deployment,value.result.id,value.target.session_hash,value.target.turn_hash,data])?;
        if inserted == 0 {
            let old: String = tx.query_row(
                "SELECT data FROM ow_observation_evaluations WHERE deployment=? AND id=?",
                params![value.deployment, value.result.id],
                |r| r.get(0),
            )?;
            ensure!(
                serde_json::from_str::<crate::evaluations::Evaluation>(&old)? == value,
                "Evaluation identity conflict"
            );
        }
        tx.commit()?;
        Ok(if inserted == 0 {
            Write::Duplicate
        } else {
            Write::Inserted
        })
    }
    #[cfg(feature = "evaluations")]
    fn evaluations(&mut self, q: &crate::evaluations::Query) -> Result<crate::evaluations::Page> {
        q.validate()?;
        if self.db.query_row(
            "SELECT count(*) FROM sqlite_master WHERE name='ow_observation_evaluations'",
            [],
            |r| r.get::<_, i64>(0),
        )? == 0
        {
            return Ok(crate::evaluations::Page {
                results: vec![],
                next: None,
            });
        }
        let mut stmt=self.db.prepare("SELECT cursor,data FROM ow_observation_evaluations WHERE deployment=? AND session_hash=? AND turn_hash=? AND cursor>? ORDER BY cursor LIMIT ?")?;
        let rows = stmt.query_map(
            params![
                q.deployment,
                q.session_hash,
                q.turn_hash,
                q.after as i64,
                i64::from(q.limit) + 1
            ],
            |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)),
        )?;
        let rows = rows
            .map(|r| {
                let (cursor, data) = r?;
                Ok(crate::evaluations::Row {
                    cursor: cursor as u64,
                    evaluation: serde_json::from_str(&data)?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        crate::evaluations::page(rows, q)
    }

    fn timeline(&mut self, q: &crate::query::Query) -> Result<crate::query::Page> {
        q.validate()?;
        let tx = self.db.transaction()?;
        let scopes = serde_json::to_string(&q.scopes)?;
        let max:i64=tx.query_row("SELECT COALESCE(max(cursor),0) FROM ow_observation_steps WHERE deployment=?1 AND (session_hash,json_extract(data,'$.turn_hash')) IN(SELECT json_extract(value,'$.session_hash'),json_extract(value,'$.turn_hash') FROM json_each(?2))",params![q.deployment,scopes],|r|r.get(0))?;
        let through = q.through.unwrap_or(max as u64);
        let rows = {
            let mut stmt=tx.prepare("SELECT cursor,data FROM ow_observation_steps WHERE deployment=?1 AND (session_hash,json_extract(data,'$.turn_hash')) IN(SELECT json_extract(value,'$.session_hash'),json_extract(value,'$.turn_hash') FROM json_each(?2)) AND cursor>?3 AND cursor<=?4 ORDER BY cursor LIMIT ?5")?;
            let rows = stmt.query_map(
                params![
                    q.deployment,
                    scopes,
                    q.after as i64,
                    through as i64,
                    i64::from(q.limit) + 1
                ],
                |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)),
            )?;
            rows.map(|r| {
                let (c, s) = r?;
                Ok((c, serde_json::from_str(&s)?))
            })
            .collect::<Result<Vec<_>>>()?
        };
        tx.commit()?;
        crate::query::page(rows, through, q)
    }
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
