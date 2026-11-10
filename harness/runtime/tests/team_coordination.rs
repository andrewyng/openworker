use openworker_runtime::team_coordination::*;
use serde_json::json;
use std::{cell::Cell, collections::BTreeSet};
fn roster(communication: Communication) -> Roster {
    Roster::new(
        Instance {
            agent_type: "coordinator".into(),
            agent_id: "lead".into(),
            session_id: "s_lead".into(),
            phase: Phase::Ready,
        },
        BTreeSet::from(["builder".into(), "verifier".into()]),
        Policy {
            communication,
            ..Default::default()
        },
    )
    .unwrap()
}
fn request(id: &str, kind: &str) -> SpawnRequest {
    SpawnRequest {
        sandbox: None,
        source: None,
        agent_type: kind.into(),
        agent_id: id.into(),
        message: format!("Task for {id}"),
        title: None,
        background: None,
        context: Default::default(),
        workspace: Default::default(),
        model: None,
    }
}
fn add(r: &mut Roster, id: &str, kind: &str) {
    let sid = format!("s_{id}");
    r.reserve(request(id, kind), || sid.clone()).unwrap();
    r.ready(id, &sid).unwrap();
}
fn recipients(r: &Roster, author: &str, body: &str) -> Vec<String> {
    r.board_recipients(author, body)
        .unwrap()
        .into_iter()
        .map(|i| i.agent_id)
        .collect()
}
#[test]
fn twenty_workers_have_distinct_identities_and_only_explicit_mentions_deliver() {
    let mut r = roster(Default::default());
    for n in 0..10 {
        add(&mut r, &format!("builder-{n}"), "builder");
        add(&mut r, &format!("verifier-{n}"), "verifier");
    }
    assert_eq!(
        recipients(&r, "lead", "@builder-3 please build; @verifier-4 check it."),
        ["builder-3", "verifier-4"]
    );
    assert_eq!(recipients(&r, "builder-3", "@lead API ready"), ["lead"]);
    assert!(recipients(&r, "builder-3", "Build done. Ownership/status changed.").is_empty());
    assert!(r
        .board_recipients("lead", "@builder ambiguous type")
        .is_err());
}
#[test]
fn spawn_retry_after_snapshot_roundtrip_keeps_original_session_without_allocation() {
    let mut r = roster(Default::default());
    let allocated = Cell::new(0);
    let first = r
        .reserve(request("builder-ui", "builder"), || {
            allocated.set(allocated.get() + 1);
            "s_original".into()
        })
        .unwrap();
    assert!(first.created);
    r.ready("builder-ui", "s_original").unwrap();
    let snapshot = serde_json::to_string(&r).unwrap();
    let mut restored: Roster = serde_json::from_str(&snapshot).unwrap();
    let again = restored
        .reserve(request("builder-ui", "builder"), || {
            allocated.set(allocated.get() + 1);
            "s_duplicate".into()
        })
        .unwrap();
    assert!(!again.created);
    assert_eq!(again.instance.session_id, "s_original");
    assert_eq!(allocated.get(), 1);
    restored.close("builder-ui").unwrap();
    let old = restored
        .reserve(request("builder-ui", "builder"), || {
            panic!("closed identity must not respawn")
        })
        .unwrap();
    assert_eq!(old.instance.phase, Phase::Closed);
}
#[test]
fn conflicting_spawn_type_task_or_session_never_rebinds_an_identity() {
    let mut r = roster(Default::default());
    add(&mut r, "builder-ui", "builder");
    let mut changed = request("builder-ui", "builder");
    changed.message = "Different work".into();
    assert!(r
        .reserve(changed, || panic!("conflict must not allocate"))
        .is_err());
    assert!(r
        .reserve(request("builder-ui", "verifier"), || panic!(
            "conflict must not allocate"
        ))
        .is_err());
    assert!(r
        .reserve(request("other", "unknown"), || panic!(
            "unknown type must not allocate"
        ))
        .is_err());
    assert!(r
        .reserve(request("other", "builder"), || "s_builder-ui".into())
        .is_err());
    assert!(r.instance("other").is_none());
    assert!(r.ready("builder-ui", "s_different").is_err());
    assert_eq!(r.instance("builder-ui").unwrap().session_id, "s_builder-ui");
}
#[test]
fn capacity_is_configurable_and_counts_the_lead() {
    let lead = Instance {
        agent_type: "lead".into(),
        agent_id: "lead".into(),
        session_id: "s_lead".into(),
        phase: Phase::Ready,
    };
    let mut r = Roster::new(
        lead.clone(),
        BTreeSet::from(["builder".into()]),
        Policy {
            max_instances: 2,
            ..Default::default()
        },
    )
    .unwrap();
    add(&mut r, "one", "builder");
    assert!(r
        .reserve(request("two", "builder"), || panic!(
            "capacity must be checked before allocation"
        ))
        .is_err());
    assert!(Roster::new(
        lead,
        BTreeSet::from(["builder".into()]),
        Policy {
            max_instances: 0,
            ..Default::default()
        }
    )
    .is_err());
}
#[test]
fn all_is_deduplicated_excludes_sender_and_is_a_post_time_snapshot() {
    let mut r = roster(Default::default());
    add(&mut r, "builder-ui", "builder");
    add(&mut r, "verifier-ui", "verifier");
    let first = recipients(&r, "builder-ui", "@all @all @lead progress");
    assert_eq!(first, ["lead", "verifier-ui"]);
    add(&mut r, "late", "builder");
    assert!(!first.contains(&"late".into()));
    assert_eq!(
        recipients(&r, "lead", "@all"),
        ["builder-ui", "late", "verifier-ui"]
    );
    r.close("late").unwrap();
    assert_eq!(
        recipients(&r, "lead", "@all"),
        ["builder-ui", "verifier-ui"]
    );
}
#[test]
fn mention_parsing_avoids_email_code_and_escaped_tokens_but_reports_bad_targets() {
    let mut r = roster(Default::default());
    add(&mut r, "builder-ui", "builder");
    assert!(recipients(
        &r,
        "lead",
        r"Email user@builder-ui.test; `@all`; \@builder-ui."
    )
    .is_empty());
    assert!(recipients(&r, "lead", "```text\n@all\n``` @builder-ui").contains(&"builder-ui".into()));
    assert!(r.board_recipients("lead", "@missing please work").is_err());
    assert!(r.board_recipients("foreign", "@all").is_err());
    r.close("builder-ui").unwrap();
    assert!(r.board_recipients("lead", "@builder-ui").is_err());
}
#[test]
fn agent_types_and_instances_cannot_claim_the_broadcast_identifier() {
    let mut r = roster(Default::default());
    assert!(r
        .reserve(request("all", "builder"), || panic!("invalid identifier"))
        .is_err());
    assert!(r
        .reserve(request("bad id", "builder"), || panic!(
            "invalid identifier"
        ))
        .is_err());
}
#[test]
fn communication_modes_are_symmetric_and_never_reroute_direct_messages() {
    for board in [false, true] {
        for direct_messages in [false, true] {
            let mut r = roster(Communication {
                board,
                direct_messages,
            });
            add(&mut r, "worker", "builder");
            for (from, to) in [("lead", "worker"), ("worker", "lead")] {
                assert_eq!(
                    r.board_recipients(from, &format!("@{to} hello")).is_ok(),
                    board
                );
                assert_eq!(
                    r.direct_recipient(from, to, "hello").is_ok(),
                    direct_messages
                );
            }
            // Spawn reservation remains available when both conversation paths are off.
            assert!(
                r.reserve(request("next", "builder"), || "s_next".into())
                    .unwrap()
                    .created
            );
        }
    }
    assert_eq!(
        serde_json::to_value(Communication::default()).unwrap(),
        json!({"board":true,"directMessages":false})
    );
}
#[test]
fn busy_idle_waiting_and_paused_inboxes_have_explicit_delivery_decisions() {
    assert_eq!(delivery(Activity::Busy), Delivery::NextModelCall);
    assert_eq!(delivery(Activity::Idle), Delivery::Wake);
    assert_eq!(delivery(Activity::Waiting), Delivery::Wake);
    for state in [Activity::Paused, Activity::Stopped, Activity::Starting] {
        assert_eq!(delivery(state), Delivery::Retain);
    }
}
fn row(n: u64) -> BoardMessage {
    BoardMessage {
        id: format!("msg_{n}"),
        sequence: n,
        sender: "lead".into(),
        body: format!("Message {n}"),
        recipients: vec![],
    }
}
fn query(anchor: Option<&str>, direction: Direction, limit: usize) -> HistoryQuery {
    HistoryQuery {
        message_id: anchor.map(str::to_owned),
        direction,
        limit,
    }
}
#[test]
fn history_before_pages_are_exclusive_chronological_and_nonoverlapping() {
    let q = query(Some("msg_21"), Direction::Before, 10);
    let first = history_page(&q, Some(21), (10..=20).rev().map(row).collect()).unwrap();
    assert_eq!(
        first
            .messages
            .iter()
            .map(|m| m.sequence)
            .collect::<Vec<_>>(),
        (11..=20).collect::<Vec<_>>()
    );
    assert!(first.has_more);
    assert_eq!(first.next_message_id.as_deref(), Some("msg_11"));
    let next = history_page(
        &query(first.next_message_id.as_deref(), Direction::Before, 10),
        Some(11),
        (1..=10).rev().map(row).collect(),
    )
    .unwrap();
    assert_eq!(
        next.messages.iter().map(|m| m.sequence).collect::<Vec<_>>(),
        (1..=10).collect::<Vec<_>>()
    );
    assert!(!next.has_more);
}
#[test]
fn history_after_and_latest_have_stable_continuation_and_bounded_defaults() {
    let p = history_page(
        &query(Some("msg_10"), Direction::After, 20),
        Some(10),
        (11..=31).map(row).collect(),
    )
    .unwrap();
    assert_eq!(p.messages.len(), 20);
    assert_eq!(p.next_message_id.as_deref(), Some("msg_30"));
    let latest: HistoryQuery = serde_json::from_value(json!({})).unwrap();
    assert_eq!(latest.limit, 20);
    let p = history_page(&latest, None, (31..=50).rev().map(row).collect()).unwrap();
    assert_eq!(p.messages.first().unwrap().sequence, 31);
    assert_eq!(p.messages.last().unwrap().sequence, 50);
    assert!(!p.has_more);
}
#[test]
fn invalid_history_anchor_order_duplicates_and_oversized_reads_are_rejected() {
    let q = query(Some("msg_10"), Direction::After, 10);
    assert!(history_page(&q, None, vec![]).is_err());
    assert!(history_page(&q, Some(0), vec![]).is_err());
    assert!(history_page(&q, Some(10), vec![row(10)]).is_err());
    assert!(history_page(&q, Some(10), vec![row(12), row(11)]).is_err());
    assert!(history_page(&q, Some(10), vec![row(11), row(11)]).is_err());
    assert!(history_page(&q, Some(10), (11..=22).map(row).collect()).is_err());
    assert!(history_page(&query(None, Direction::Before, 51), None, vec![]).is_err());
    assert!(history_page(&query(None, Direction::Before, 0), None, vec![]).is_err());
}
#[test]
fn retiring_members_hold_capacity_until_closed_and_identities_never_recycle() {
    let mut r = Roster::new(
        Instance {
            agent_type: "lead".into(),
            agent_id: "lead".into(),
            session_id: "s_lead".into(),
            phase: Phase::Ready,
        },
        ["builder".into()].into(),
        Policy {
            max_instances: 2,
            ..Default::default()
        },
    )
    .unwrap();
    add(&mut r, "first", "builder");
    r.retire("first").unwrap();
    assert!(r
        .reserve(request("second", "builder"), || panic!(
            "retiring capacity is still held"
        ))
        .is_err());
    assert!(r.ready("first", "s_first").is_err());
    assert!(r.direct_recipient("lead", "first", "new task").is_err());
    r.close("first").unwrap();
    add(&mut r, "second", "builder");
    assert_eq!(
        r.reserve(request("first", "builder"), || panic!(
            "historical id must never allocate"
        ))
        .unwrap()
        .instance
        .phase,
        Phase::Closed
    );
}
