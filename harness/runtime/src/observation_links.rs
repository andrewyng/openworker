//! Durable spawn provenance. Contains identifiers only, never a child brief.
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SpawnOrigin {
    pub parent_session_id: String,
    pub parent_turn_id: String,
    pub parent_call_id: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ChildLink {
    pub origin: SpawnOrigin,
    pub child_session_id: String,
    /// First accepted task only; later inbox turns are not charged to the spawn.
    #[serde(default)]
    pub child_turn_id: Option<String>,
    pub agent_id: String,
    pub phase: crate::team_coordination::Phase,
}
pub fn children(doc: &crate::agent_team::Document, session: &str, turn: &str) -> Vec<ChildLink> {
    doc.trace_origins
        .iter()
        .filter(|(_, o)| o.parent_session_id == session && o.parent_turn_id == turn)
        .filter_map(|(id, origin)| {
            doc.roster.instance(id).map(|i| ChildLink {
                origin: origin.clone(),
                child_session_id: i.session_id.clone(),
                child_turn_id: None,
                agent_id: id.clone(),
                phase: i.phase,
            })
        })
        .collect()
}

/// A bounded selection of causally linked initial tasks. No transcript is read.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub struct Run {
    pub session_id: String,
    pub turn_id: String,
}
#[derive(Debug, Serialize, Deserialize)]
pub struct Subtree {
    pub runs: Vec<Run>,
    pub selection_truncated: bool,
    pub scope: String,
}
pub fn subtree(
    root: Run,
    mut children: impl FnMut(&Run) -> anyhow::Result<Vec<ChildLink>>,
) -> anyhow::Result<Subtree> {
    let mut runs = vec![root.clone()];
    let mut seen = std::collections::BTreeSet::from([root]);
    let mut truncated = false;
    let mut index = 0;
    while index < runs.len() {
        for child in children(&runs[index])? {
            let Some(turn_id) = child.child_turn_id else {
                continue;
            };
            let run = Run {
                session_id: child.child_session_id,
                turn_id,
            };
            if seen.contains(&run) {
                continue;
            }
            if runs.len() == 25 {
                truncated = true;
                continue;
            }
            seen.insert(run.clone());
            runs.push(run);
        }
        index += 1;
    }
    Ok(Subtree {
        runs,
        selection_truncated: truncated,
        scope: "root_and_visible_spawned_initial_turns".into(),
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn nested_links_are_bounded_and_cycles_do_not_double_count() {
        let root = Run {
            session_id: "s0".into(),
            turn_id: "t".into(),
        };
        let result = subtree(root, |r| {
            Ok((0..40)
                .map(|n| ChildLink {
                    origin: SpawnOrigin {
                        parent_session_id: r.session_id.clone(),
                        parent_turn_id: r.turn_id.clone(),
                        parent_call_id: None,
                    },
                    child_session_id: format!("s{n}"),
                    child_turn_id: Some("t".into()),
                    agent_id: format!("a{n}"),
                    phase: crate::team_coordination::Phase::Ready,
                })
                .collect())
        })
        .unwrap();
        assert_eq!(result.runs.len(), 25);
        assert!(result.selection_truncated);
        assert_eq!(
            result
                .runs
                .iter()
                .collect::<std::collections::BTreeSet<_>>()
                .len(),
            25
        );
    }
}
