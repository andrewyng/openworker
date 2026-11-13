use std::{
    fs,
    process::{Command, Output},
};
const PRIMARY: &str = env!("CARGO_BIN_EXE_owcluster");
const LEGACY: &str = env!("CARGO_BIN_EXE_ows");
fn run(bin: &str, args: &[&str]) -> Output {
    Command::new(bin).args(args).output().unwrap()
}
#[test]
fn both_names_have_the_same_help_version_and_errors() {
    for args in [
        vec!["--help"],
        vec!["--version"],
        vec!["deploy", "aws", "up", "--help"],
        vec!["deploy", "azure", "up", "--help"],
        vec!["configuration", "--help"],
        vec!["--not-a-real-option"],
    ] {
        let primary = run(PRIMARY, &args);
        let legacy = run(LEGACY, &args);
        assert_eq!(primary.status.code(), legacy.status.code());
        assert_eq!(primary.stdout, legacy.stdout);
        assert_eq!(primary.stderr, legacy.stderr);
        let text = format!(
            "{}{}",
            String::from_utf8_lossy(&primary.stdout),
            String::from_utf8_lossy(&primary.stderr)
        );
        assert!(text.contains("owcluster"), "{text}");
    }
}
#[test]
fn aliases_share_the_existing_config_path_without_migration() {
    let home = tempfile::tempdir().unwrap();
    let call = |bin, args: &[&str]| {
        Command::new(bin)
            .env("HOME", home.path())
            .env("USERPROFILE", home.path())
            .args(args)
            .output()
            .unwrap()
    };
    let added = call(
        PRIMARY,
        &[
            "--url",
            "https://cluster.example.test",
            "--namespace",
            "demo",
            "context",
            "add",
            "staging",
        ],
    );
    assert!(
        added.status.success(),
        "{}",
        String::from_utf8_lossy(&added.stderr)
    );
    let path = home.path().join(".config/openworker/ows/config.json");
    let original = fs::read(&path).unwrap();
    for bin in [PRIMARY, LEGACY] {
        let listed = call(bin, &["context", "list"]);
        assert!(listed.status.success());
        let config: serde_json::Value = serde_json::from_slice(&listed.stdout).unwrap();
        assert_eq!(config["selected"], "staging");
        assert_eq!(config["contexts"]["staging"]["namespace"], "demo");
    }
    assert_eq!(fs::read(path).unwrap(), original);
    assert!(!home.path().join(".config/openworker/owcluster").exists());
}
