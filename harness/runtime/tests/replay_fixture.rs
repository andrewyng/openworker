#![cfg(unix)]

use openworker_runtime::replay_fixture::{restore, File, Fixture};
use std::{fs, os::unix::fs::symlink};

fn fixture(items: &[(&str, &str)]) -> Fixture {
    Fixture(
        items
            .iter()
            .map(|(path, text)| File {
                path: (*path).into(),
                text: (*text).into(),
            })
            .collect(),
    )
}

#[test]
fn restores_identical_fixtures_in_separate_workspaces() {
    let left = tempfile::tempdir().unwrap();
    let right = tempfile::tempdir().unwrap();
    let files = fixture(&[
        ("settings.txt", "mode=test"),
        ("nested/request.txt", "same bytes\n"),
    ]);
    assert!(restore(left.path(), files.clone(), true).is_err());
    let restored = restore(left.path(), files.clone(), false).unwrap();
    assert_eq!(restore(left.path(), files.clone(), true).unwrap(), restored);
    let reversed = Fixture(files.0.iter().rev().cloned().collect());
    assert_eq!(restore(right.path(), reversed, false).unwrap(), restored);
    fs::write(
        left.path().join("nested/request.txt"),
        "baseline changed it",
    )
    .unwrap();
    assert_eq!(
        fs::read_to_string(right.path().join("nested/request.txt")).unwrap(),
        "same bytes\n"
    );
    assert!(restore(left.path(), files.clone(), true).is_err());
    assert!(restore(left.path(), files, false).is_err());
    assert_eq!(
        fs::read_to_string(left.path().join("nested/request.txt")).unwrap(),
        "baseline changed it"
    );
}

#[test]
fn rejects_traversal_symlinks_and_conflicting_paths() {
    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    fs::write(outside.path().join("never-touch"), "original").unwrap();
    symlink(outside.path(), root.path().join("linked")).unwrap();
    symlink(
        outside.path().join("never-touch"),
        root.path().join("direct"),
    )
    .unwrap();
    for items in [
        vec![("../never-touch", "changed")],
        vec![("linked/never-touch", "changed")],
        vec![("direct", "changed")],
        vec![("nested", "file"), ("nested/child", "file")],
        vec![("DUPLICATE", "file"), ("duplicate", "file")],
        vec![("Nested/first", "file"), ("nested/second", "file")],
        vec![(".git/config", "changed")],
    ] {
        assert!(restore(root.path(), fixture(&items), false).is_err());
    }
    assert_eq!(
        fs::read_to_string(outside.path().join("never-touch")).unwrap(),
        "original"
    );
    assert!(!root.path().join("nested").exists());
}
