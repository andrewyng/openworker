use crate::{
    auth::{self, Action},
    db::Database,
};
use anyhow::{ensure, Result};
use openworker_runtime::attachments::*;
impl Database {
    pub(crate) fn input_blob(&mut self, sid: &str, id: &str, request: Upload) -> Result<Blob> {
        let stat = matches!(&request, Upload::Stat);
        let mut tx = self.client.transaction()?;
        auth::guard(
            &mut tx,
            self.auth.as_ref(),
            if stat { Action::Read } else { Action::Submit },
            Some(sid),
            None,
        )?;
        let session = tx
            .query_opt("SELECT data FROM sessions WHERE id=$1 FOR UPDATE", &[&sid])?
            .ok_or_else(|| anyhow::anyhow!("Session unavailable"))?;
        let closed = session.get::<_, serde_json::Value>(0)["closed"] == true;
        let writable = !stat && !closed;
        if writable {
            tx.execute("DELETE FROM input_attachments WHERE session_id=$1 AND NOT referenced AND created<clock_timestamp()-interval '24 hours'",&[&sid])?;
        }
        let old = tx
            .query_opt(
                "SELECT metadata,data,sealed FROM input_attachments WHERE session_id=$1 AND id=$2",
                &[&sid, &id],
            )?
            .map(|r| -> Result<_> {
                Ok(Blob {
                    metadata: serde_json::from_value(r.get(0))?,
                    bytes: r.get(1),
                    sealed: r.get(2),
                })
            })
            .transpose()?;
        if closed && !stat {
            ensure!(
                matches!(&request, Upload::Begin { .. }) && old.as_ref().is_some_and(|b| b.sealed),
                "Session closed"
            );
        }
        if old.is_none() {
            if let Upload::Begin { metadata } = &request {
                let count: i64 = tx
                    .query_one(
                        "SELECT count(*) FROM input_attachments WHERE session_id=$1",
                        &[&sid],
                    )?
                    .get(0);
                ensure!(
                    count < MAX_SESSION_FILES as i64,
                    "Session attachment count limit exceeded"
                );
                let total:i64=tx.query_one("SELECT COALESCE(SUM((metadata->>'size')::bigint),0)::bigint FROM input_attachments WHERE session_id=$1",&[&sid])?.get(0);
                ensure!(
                    total.saturating_add(metadata.size.try_into()?) <= MAX_SESSION as i64,
                    "Session attachment quota exceeded"
                );
            }
        }
        let blob = apply(id, old, request)?;
        if writable {
            tx.execute("INSERT INTO input_attachments(session_id,id,metadata,data,sealed) VALUES($1,$2,$3,$4,$5) ON CONFLICT(session_id,id) DO UPDATE SET data=excluded.data,sealed=excluded.sealed",&[&sid,&id,&serde_json::to_value(&blob.metadata)?,&blob.bytes,&blob.sealed])?;
        }
        tx.commit()?;
        Ok(blob)
    }
}

pub(crate) fn validate_references(
    tx: &mut postgres::Transaction<'_>,
    sid: &str,
    parts: &[Part],
) -> Result<()> {
    openworker_runtime::attachments::validate_references(parts, |id| {
        let row = tx
            .query_opt(
                "SELECT metadata,sealed FROM input_attachments WHERE session_id=$1 AND id=$2",
                &[&sid, &id],
            )?
            .ok_or_else(|| anyhow::anyhow!("Attachment not found in this session"))?;
        ensure!(row.get::<_, bool>(1), "Attachment upload is incomplete");
        Ok(serde_json::from_value(row.get(0))?)
    })
}

pub(crate) fn retain(tx: &mut postgres::Transaction<'_>, sid: &str, parts: &[Part]) -> Result<()> {
    for part in parts {
        if let Part::Image { attachment_id } | Part::Document { attachment_id } = part {
            tx.execute(
                "UPDATE input_attachments SET referenced=true WHERE session_id=$1 AND id=$2",
                &[&sid, &attachment_id],
            )?;
        }
    }
    Ok(())
}
