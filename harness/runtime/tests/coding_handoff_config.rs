use openworker_runtime::{
    coding_handoff::config::{Choice, TeamConfig},
    team_workspace::Selection,
};
use serde_json::json;
fn config() -> TeamConfig {
    serde_json::from_value(json!({"source":{"repository":"org/game","directory":"source","object_format":"sha1","commit":"a".repeat(40)},"agent_types":{"builder":{"sandbox":{"mode":"new","profile":"small@1","cpu":1,"memory_mb":512},"source":"initial"},"verifier":{"sandbox":{"mode":"new","profile":"small@1","cpu":1,"memory_mb":512},"source":"require_submission"}}})).unwrap()
}
#[test]
fn isolated_defaults_pin_independent_paths_without_starting_or_granting_sandboxes() {
    let c = config();
    c.validate(&["builder".into(), "verifier".into()].into())
        .unwrap();
    let (a, source) = c
        .resolve("at_one", "builder", "ui", Selection::default(), None)
        .unwrap();
    assert_eq!(
        a,
        Selection::Directory {
            path: "checkouts/at_one/ui".into()
        }
    );
    assert_eq!(source, Some(Choice::Initial));
    assert_ne!(
        c.resolve("at_two", "builder", "ui", Selection::default(), None)
            .unwrap()
            .0,
        a
    );
    assert_ne!(
        c.resolve("at_one", "builder", "api", Selection::default(), None)
            .unwrap()
            .0,
        a
    );
    let path = Selection::Directory {
        path: "specific/checkout".into(),
    };
    assert_eq!(
        c.resolve("at_one", "builder", "ui", path.clone(), None)
            .unwrap()
            .0,
        path
    );
    assert_eq!(
        c.resolve("at_one", "custom", "helper", Selection::default(), None)
            .unwrap(),
        (Selection::default(), None)
    );
}
#[test]
fn verifier_requires_an_actual_submission_reference_and_not_initial_base() {
    let c = config();
    assert!(c
        .resolve("at_one", "verifier", "check", Selection::default(), None)
        .is_err());
    assert!(c
        .resolve(
            "at_one",
            "verifier",
            "check",
            Selection::default(),
            Some(Choice::Initial)
        )
        .is_err());
    let choice = Choice::Submission {
        id: "handoff_original".into(),
    };
    assert_eq!(
        c.resolve(
            "at_one",
            "verifier",
            "check",
            Selection::default(),
            Some(choice.clone())
        )
        .unwrap()
        .1,
        Some(choice)
    );
    // The reference is data; the catalog must still check team ownership and
    // readiness, original artifact authority, selected profile and destination.
    assert!(serde_json::from_value::<Choice>(
        json!({"kind":"submission","id":"h_one","url":"https://elsewhere"})
    )
    .is_err());
}
#[test]
fn defaults_reject_moving_source_unregistered_types_linked_metadata_and_reserved_paths() {
    let c = config();
    assert!(c.validate(&["builder".into()].into()).is_err());
    let mut changed = c.clone();
    changed.source.commit = "HEAD".into();
    assert!(changed
        .validate(&["builder".into(), "verifier".into()].into())
        .is_err());
    assert!(c
        .resolve(
            "at_one",
            "builder",
            "ui",
            Selection::Worktree {
                path: "workers/ui".into(),
                repository: "source".into(),
                branch: "ui".into(),
                base: "HEAD".into()
            },
            None
        )
        .is_err());
    for path in [
        "../outside",
        "/host/path",
        ".openworker/imports/other",
        "C:\\outside",
    ] {
        assert!(c
            .resolve(
                "at_one",
                "builder",
                "ui",
                Selection::Directory { path: path.into() },
                None
            )
            .is_err());
    }
}
