//! Restartable orchestration of native lifecycle stages. A stage receipt does not
//! replace provider ownership receipts. Every resumed effect is observed first.
use super::*;
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub(super) enum Stage {
    Bundle,
    Preflight,
    Permissions,
    Control,
    Foundation,
    IdentityBindings,
    Cluster,
    Images,
    PrivateAccess,
    Platform,
    OpenShell,
    Secrets,
    Service,
    Verify,
}
pub(super) const ORDER: [Stage; 14] = [
    Stage::Bundle,
    Stage::Permissions,
    Stage::Preflight,
    Stage::Control,
    Stage::Foundation,
    Stage::IdentityBindings,
    Stage::Cluster,
    Stage::Images,
    Stage::PrivateAccess,
    Stage::Platform,
    Stage::OpenShell,
    Stage::Secrets,
    Stage::Service,
    Stage::Verify,
];
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(super) enum Phase {
    Intent,
    Pending,
    Complete,
    Blocked,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Step {
    pub request_id: String,
    pub phase: Phase,
    pub receipt: Option<Value>,
    pub action: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Journal {
    pub schema_version: u16,
    pub review_digest: String,
    pub deployment_id: String,
    pub stages: BTreeMap<Stage, Step>,
}
#[derive(Debug)]
pub(super) enum Observation {
    /// Independently proven no request/effect; native receipt contract permits start.
    Absent,
    Pending,
    Complete(Value),
    Blocked(String),
    /// No deletion/recreation/replay is permitted on this observation.
    Unknown(String),
}
pub(super) trait Driver {
    async fn observe(&mut self, stage: Stage, step: Option<&Step>) -> Result<Observation>;
    async fn execute(&mut self, stage: Stage, request_id: &str) -> Result<Observation>;
}
impl Journal {
    pub fn open(path: &Path, review: &Review) -> Result<Self> {
        if !path.exists() {
            return Ok(Self {
                schema_version: 1,
                review_digest: review.digest.clone(),
                deployment_id: review.deployment_id.clone(),
                stages: BTreeMap::new(),
            });
        }
        let j: Self = serde_json::from_slice(&fs::read(path)?)?;
        ensure!(
            j.schema_version == 1
                && j.review_digest == review.digest
                && j.deployment_id == review.deployment_id,
            "Deployment journal identity mismatch"
        );
        for step in j.stages.values() {
            ensure!(
                uuid::Uuid::parse_str(&step.request_id).is_ok_and(|id| !id.is_nil()),
                "Invalid stage request receipt"
            );
        }
        Ok(j)
    }
    pub fn complete(&self) -> bool {
        ORDER.iter().all(|s| {
            self.stages
                .get(s)
                .is_some_and(|v| v.phase == Phase::Complete)
        })
    }
}
fn record(
    j: &mut Journal,
    path: &Path,
    stage: Stage,
    phase: Phase,
    receipt: Option<Value>,
    action: Option<String>,
) -> Result<()> {
    let step = j.stages.entry(stage).or_insert_with(|| Step {
        request_id: uuid::Uuid::new_v4().to_string(),
        phase: Phase::Intent,
        receipt: None,
        action: None,
    });
    step.phase = phase;
    step.receipt = receipt;
    step.action = action;
    write_private(path, j)
}
/// Advances at most one cloud stage. Effects are never retried because a local
/// process failed: the native adapter must first prove no-effect or completion.
pub(super) async fn advance(
    j: &mut Journal,
    path: &Path,
    driver: &mut impl Driver,
) -> Result<bool> {
    let Some(stage) = ORDER
        .iter()
        .find(|s| j.stages.get(s).is_none_or(|v| v.phase != Phase::Complete))
        .copied()
    else {
        return Ok(true);
    };
    let observed = driver.observe(stage, j.stages.get(&stage)).await?;
    let outcome = match observed {
        Observation::Absent => {
            record(j, path, stage, Phase::Intent, None, None)?;
            let id = j.stages[&stage].request_id.clone();
            // On failure the durable intent remains. The next invocation calls
            // observe, never execute directly.
            driver.execute(stage, &id).await?
        }
        other => other,
    };
    match outcome {
        Observation::Complete(receipt) => {
            record(j, path, stage, Phase::Complete, Some(receipt), None)?
        }
        Observation::Pending => record(j, path, stage, Phase::Pending, None, None)?,
        Observation::Blocked(action) => {
            record(j, path, stage, Phase::Blocked, None, Some(action.clone()))?;
            bail!("{action}");
        }
        Observation::Unknown(action) => {
            record(j, path, stage, Phase::Pending, None, Some(action.clone()))?;
            bail!("Outcome unknown; inspect original stage {stage:?}. {action}");
        }
        Observation::Absent => bail!(
            "Stage did not provide a durable outcome after execution; reconcile before continuing"
        ),
    }
    Ok(j.complete())
}
#[cfg(test)]
mod tests {
    use super::*;
    struct Fake {
        effects: usize,
        lost: bool,
        remote: bool,
        unknown: bool,
    }
    impl Driver for Fake {
        async fn observe(&mut self, _: Stage, _: Option<&Step>) -> Result<Observation> {
            Ok(if self.unknown {
                Observation::Unknown("read failed".into())
            } else if self.remote {
                Observation::Complete(json!({"original":true}))
            } else {
                Observation::Absent
            })
        }
        async fn execute(&mut self, _: Stage, _: &str) -> Result<Observation> {
            self.effects += 1;
            self.remote = true;
            if self.lost {
                bail!("lost after effect")
            }
            Ok(Observation::Pending)
        }
    }
    fn journal() -> Journal {
        Journal {
            schema_version: 1,
            review_digest: "digest".into(),
            deployment_id: "original".into(),
            stages: BTreeMap::new(),
        }
    }
    #[tokio::test]
    async fn lost_effect_is_reconciled_after_process_restart_without_duplicate() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("journal.json");
        let mut j = journal();
        let mut d = Fake {
            effects: 0,
            lost: true,
            remote: false,
            unknown: false,
        };
        assert!(advance(&mut j, &p, &mut d).await.is_err());
        assert_eq!(j.stages[&Stage::Bundle].phase, Phase::Intent);
        let id = j.stages[&Stage::Bundle].request_id.clone();
        let mut j: Journal = serde_json::from_slice(&fs::read(&p).unwrap()).unwrap();
        advance(&mut j, &p, &mut d).await.unwrap();
        assert_eq!(d.effects, 1);
        assert_eq!(j.stages[&Stage::Bundle].request_id, id);
        assert_eq!(j.stages[&Stage::Bundle].phase, Phase::Complete);
    }
    #[tokio::test]
    async fn unknown_observation_never_executes_and_blocks_following_stages() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("journal.json");
        let mut j = journal();
        let mut d = Fake {
            effects: 0,
            lost: false,
            remote: false,
            unknown: true,
        };
        assert!(advance(&mut j, &p, &mut d).await.is_err());
        assert_eq!(d.effects, 0);
        assert_eq!(j.stages.len(), 1);
    }
}

#[cfg(test)]
mod boundary_tests {
    use super::*;
    struct Fault {
        effects: BTreeMap<Stage, usize>,
        fail: Stage,
        lost: bool,
    }
    impl Driver for Fault {
        async fn observe(&mut self, s: Stage, _: Option<&Step>) -> Result<Observation> {
            Ok(if self.effects.contains_key(&s) {
                Observation::Complete(json!({"original":true}))
            } else {
                Observation::Absent
            })
        }
        async fn execute(&mut self, s: Stage, _: &str) -> Result<Observation> {
            *self.effects.entry(s).or_default() += 1;
            if s == self.fail && !self.lost {
                self.lost = true;
                bail!("crash after effect")
            }
            Ok(Observation::Complete(json!({"ready":true})))
        }
    }
    #[tokio::test]
    async fn every_stage_survives_restart_without_repeating_effects() {
        for failed in ORDER {
            let temp = tempfile::tempdir().unwrap();
            let path = temp.path().join("journal.json");
            let mut j = Journal {
                schema_version: 1,
                review_digest: "r".into(),
                deployment_id: "d".into(),
                stages: BTreeMap::new(),
            };
            let mut driver = Fault {
                effects: BTreeMap::new(),
                fail: failed,
                lost: false,
            };
            for _ in 0..ORDER.len() + 2 {
                match advance(&mut j, &path, &mut driver).await {
                    Ok(true) => break,
                    Ok(false) => (),
                    Err(_) => {
                        j = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
                    }
                }
            }
            assert!(j.complete(), "{failed:?}");
            assert_eq!(driver.effects.len(), ORDER.len());
            assert!(driver.effects.values().all(|n| *n == 1));
        }
    }
}
