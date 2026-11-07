//! Trusted, bounded raw read seam. Public services must authorize canonical runs.
use crate::{Step, StoredStep};
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
pub const MAX_SUMMARY_STEPS: u16 = 4096;
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Scope {
    pub session_hash: String,
    pub turn_hash: String,
}
#[derive(Clone, Debug)]
pub struct Query {
    pub deployment: String,
    pub scopes: Vec<Scope>,
    pub after: u64,
    pub through: Option<u64>,
    pub limit: u16,
}
impl Query {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            crate::identifier(&self.deployment)
                && !self.scopes.is_empty()
                && self.scopes.len() <= 25
                && self
                    .scopes
                    .iter()
                    .all(|s| crate::hex_id(&s.session_hash, 64) && crate::hex_id(&s.turn_hash, 64))
                && self.after <= i64::MAX as u64
                && self
                    .through
                    .is_none_or(|n| n <= i64::MAX as u64 && n >= self.after)
                && (1..=MAX_SUMMARY_STEPS).contains(&self.limit),
            "Invalid recording query"
        );
        Ok(())
    }
}
#[derive(Debug, Serialize, Deserialize)]
pub struct Page {
    pub steps: Vec<StoredStep>,
    pub through: u64,
    pub next: Option<u64>,
}
pub(crate) fn page(rows: Vec<(i64, Step)>, through: u64, q: &Query) -> Result<Page> {
    let limit = q.limit;
    let more = rows.len() > usize::from(limit);
    let mut steps = vec![];
    for (cursor, step) in rows.into_iter().take(limit.into()) {
        ensure!(cursor > 0, "Invalid recording cursor");
        step.validate()?;
        ensure!(
            step.deployment == q.deployment
                && q.scopes
                    .iter()
                    .any(|s| step.session_hash.as_ref() == Some(&s.session_hash)
                        && step.turn_hash.as_ref() == Some(&s.turn_hash)),
            "Recording scope mismatch"
        );
        steps.push(StoredStep {
            cursor: cursor as u64,
            step,
        });
    }
    let next = if more {
        steps.last().map(|r| r.cursor)
    } else {
        None
    };
    Ok(Page {
        steps,
        through,
        next,
    })
}
