use openworker_runtime::{bundle::Bundle, execution::Backend, package, permissions, skills};
use serde_json::{json, Value};
use std::{collections::BTreeSet, path::Path};

fn fixture() -> (tempfile::TempDir, Bundle) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::create_dir_all(root.join("coworker/skills/report-guide/references")).unwrap();
    for (path, text) in [
        (
            "coworker/manifest.md",
            include_str!("../../examples/skilled/coworker/manifest.md"),
        ),
        (
            "tools.json",
            include_str!("../../examples/skilled/tools.json"),
        ),
        (
            "coworker/skills/report-guide/SKILL.md",
            include_str!("../../examples/skilled/coworker/skills/report-guide/SKILL.md"),
        ),
        (
            "coworker/skills/report-guide/references/report-template.md",
            include_str!(
                "../../examples/skilled/coworker/skills/report-guide/references/report-template.md"
            ),
        ),
    ] {
        std::fs::write(root.join(path), text).unwrap();
    }
    let b = Bundle::load(root).unwrap();
    (tmp, b)
}
#[test]
fn shared_python_skill_contract_and_intentional_differences() {
    let f: Value = serde_json::from_str(include_str!("../../tests/skills-contract.json")).unwrap();
    for c in f["shared"].as_array().unwrap() {
        let s = skills::parse(c["text"].as_str().unwrap(), c["folder"].as_str().unwrap()).unwrap();
        assert_eq!(s.name, c["name"]);
        assert_eq!(s.description, c["description"]);
        assert_eq!(s.instructions, c["instructions"]);
        assert_eq!(json!(s.allowed_tools), c["allowed_tools"]);
    }
    for c in f["differences"].as_array().unwrap() {
        let s = skills::parse(c["text"].as_str().unwrap(), c["folder"].as_str().unwrap());
        if c["rust_reject"] == true {
            assert!(s.is_err());
            continue;
        }
        let s = s.unwrap();
        if let Some(v) = c.get("rust_description") {
            assert_eq!(json!(s.description), *v);
        }
        if let Some(v) = c.get("rust_allowed_tools") {
            assert_eq!(json!(s.allowed_tools), *v);
        }
    }
}
#[test]
fn manifest_is_versioned_strict_and_preserves_markdown() {
    let base = "---\nid: report\nname: Report\n";
    let tail = "---\nBody --- inline.\n\n---\nLast section.";
    let (_, body) = package::parse(&format!("{base}{tail}")).unwrap();
    assert!(body.contains("---\nLast"));
    for extra in [
        "schema_version: 2\n",
        "requires_folder: false\n",
        "default_permission_mode: bypass\n",
        "subagents: true\n",
        "scheduling: all\n",
        "memory_scope: learner-without-enabled-memory\n",
        "connectors: all\n",
        "models: [foo]\n",
        "skills: [x, x]\n",
        "skills: ../outside\n",
        "id: replacement\n",
    ] {
        assert!(
            package::parse(&format!("{base}{extra}{tail}")).is_err(),
            "accepted {extra}"
        );
    }
    assert!(package::parse("---\nid: x\n---\n").is_err());
    assert!(package::parse("---invalid\nid: x\n---\nBody").is_err());
    assert!(package::parse("---\nid: x\n").is_err());
    assert!(package::parse("---\nid: x\nversion: [1]\n---\nBody").is_err());
    assert!(package::parse("---\nid: x\ndescription: *missing\n---\nBody").is_err());
    let (manifest, _) = package::parse(&format!(
        "{base}memory: true\nmemory_scope: learner/workspace\nscheduling: true\n{tail}"
    ))
    .unwrap();
    assert!(manifest.memory && manifest.scheduling);
    assert_eq!(manifest.memory_scope.as_deref(), Some("learner/workspace"));
}

#[test]
fn planning_is_opt_in_and_cannot_be_shadowed_by_partner_tools() {
    let (old, _) = package::parse("---\nid: legacy\n---\nWork carefully.").unwrap();
    assert!(!old.planning);
    assert!(json!(old).get("planning").is_none());
    let (enabled, _) =
        package::parse("---\nid: planner\nplanning: true\n---\nWork carefully.").unwrap();
    assert!(enabled.planning);
    assert!(package::parse("---\nid: invalid\nplanning: {mode: bypass}\n---\nWork.").is_err());
    let (tmp, _) = fixture();
    let mut tools: Value =
        serde_json::from_str(&std::fs::read_to_string(tmp.path().join("tools.json")).unwrap())
            .unwrap();
    tools["bindings"]["update_plan"] = tools["bindings"]
        .as_object()
        .unwrap()
        .values()
        .next()
        .unwrap()
        .clone();
    std::fs::write(tmp.path().join("tools.json"), tools.to_string()).unwrap();
    assert!(Bundle::load(tmp.path()).is_err());
}

#[test]
fn team_examples_are_role_pinned_and_internal_tools_cannot_be_shadowed() {
    for (directory, role) in [
        ("team-lead", openworker_runtime::teams::Role::Lead),
        ("team-worker", openworker_runtime::teams::Role::Worker),
    ] {
        let bundle = Bundle::load(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../examples")
                .join(directory),
        )
        .unwrap();
        assert_eq!(bundle.manifest.team_role, Some(role));
        assert!(bundle.manifest.default_model.is_some());
    }
    assert!(package::parse("---\nid: x\nteam_role: admin\n---\nWork.").is_err());
    let (tmp, _) = fixture();
    let mut tools: Value =
        serde_json::from_str(&std::fs::read_to_string(tmp.path().join("tools.json")).unwrap())
            .unwrap();
    tools["bindings"]["team_assign"] = tools["bindings"]
        .as_object()
        .unwrap()
        .values()
        .next()
        .unwrap()
        .clone();
    std::fs::write(tmp.path().join("tools.json"), tools.to_string()).unwrap();
    assert!(Bundle::load(tmp.path()).is_err());
}
#[test]
fn catalog_is_lazy_selection_is_scoped_and_hints_are_not_authority() {
    let (_tmp, b) = fixture();
    let selected = skills::select(&b.skills, None).unwrap();
    let catalog = json!(skills::catalog(&b.skills, &selected)).to_string();
    assert!(!catalog.contains("REPORT_GUIDE_BODY_MARKER"));
    let prompt = b.instructions_for(&selected, &BTreeSet::new());
    assert!(!prompt.contains("REPORT_GUIDE_BODY_MARKER"));
    assert!(prompt.contains("load_skill"));
    assert!(skills::select(&b.skills, Some(&json!([])))
        .unwrap()
        .is_empty());
    for v in [
        json!(["missing"]),
        json!(["report-guide", "report-guide"]),
        json!(null),
        json!("all"),
    ] {
        assert!(skills::select(&b.skills, Some(&v)).is_err());
    }
    let loaded = b
        .skill_call(
            &selected,
            "load_skill",
            &json!({"name":"report-guide"}),
            Backend::Openshell,
        )
        .unwrap();
    assert!(loaded["instructions"]
        .as_str()
        .unwrap()
        .contains("REPORT_GUIDE_BODY_MARKER"));
    assert_eq!(
        loaded["resources_path"],
        "/opt/partner/coworker/skills/report-guide"
    );
    assert_eq!(
        loaded["allowed_tools_hint"],
        json!(["write_file", "run_shell"])
    );
    assert_eq!(b.tools.bindings.len(), 2);
    assert_eq!(
        permissions::classify(&b, "load_skill"),
        permissions::Category::Read
    );
    assert!(b
        .skill_call(
            &[],
            "load_skill",
            &json!({"name":"report-guide"}),
            Backend::Off
        )
        .unwrap()
        .get("error")
        .is_some());
    assert_eq!(b.instructions_for(&[], &BTreeSet::new()), b.instructions);
}
#[test]
fn resource_reads_are_scoped_bounded_and_never_execute_scripts() {
    let (tmp, _) = fixture();
    let root = tmp.path().join("coworker/skills/report-guide");
    std::fs::write(root.join("binary.bin"), [0xff, 0xfe]).unwrap();
    std::fs::write(root.join("large.txt"), vec![b'a'; 64001]).unwrap();
    std::fs::write(root.join("escaped.txt"), vec![1u8; 32000]).unwrap();
    std::fs::write(root.join("script.sh"), "#!/bin/sh\ntouch /must-not-run\n").unwrap();
    let b = Bundle::load(tmp.path()).unwrap();
    let selected = skills::select(&b.skills, None).unwrap();
    let call = |path: &str| {
        b.skill_call(
            &selected,
            "read_skill_resource",
            &json!({"name":"report-guide","path":path}),
            Backend::Off,
        )
        .unwrap()
    };
    assert!(call("references/report-template.md")["content"]
        .as_str()
        .unwrap()
        .contains("SKILL_REFERENCE_MARKER"));
    assert!(call("script.sh")["content"]
        .as_str()
        .unwrap()
        .contains("touch"));
    for path in [
        "../manifest.md",
        "/etc/passwd",
        "references/../../SKILL.md",
        "references//report-template.md",
        "references\\report-template.md",
        "missing",
        "binary.bin",
        "large.txt",
        "escaped.txt",
    ] {
        assert!(call(path).get("error").is_some(), "accepted {path}");
    }
    std::fs::write(root.join("references/report-template.md"), "changed").unwrap();
    assert!(b
        .skill_call(
            &selected,
            "load_skill",
            &json!({"name":"report-guide"}),
            Backend::Off
        )
        .is_err());
}

#[test]
fn oversized_escaped_skill_body_is_refused_without_partial_instructions() {
    let (tmp, _) = fixture();
    std::fs::write(
        tmp.path().join("coworker/skills/report-guide/SKILL.md"),
        format!(
            "---\nname: report-guide\ndescription: Bounded body\n---\n{}",
            "\u{1}".repeat(32000)
        ),
    )
    .unwrap();
    let b = Bundle::load(tmp.path()).unwrap();
    let result = b
        .skill_call(
            &["report-guide".into()],
            "load_skill",
            &json!({"name":"report-guide"}),
            Backend::Off,
        )
        .unwrap();
    assert!(result.get("error").is_some());
    assert!(result.get("instructions").is_none());
    assert!(result.get("skill_digest").is_none());
}
#[test]
fn unsupported_skill_metadata_and_reserved_aliases_reject() {
    for extra in [
        "permission_mode: bypass\n",
        "tools: [run_shell]\n",
        "name: replacement\n",
    ] {
        assert!(skills::parse(
            &format!("---\nname: report\ndescription: Test\n{extra}---\nBody"),
            "report"
        )
        .is_err());
    }
    let (tmp, b) = fixture();
    let mut tools = serde_json::to_value(&b.tools).unwrap();
    tools["bindings"]["load_skill"] = json!({"server":"report-tools","tool":"load_skill"});
    std::fs::write(tmp.path().join("tools.json"), tools.to_string()).unwrap();
    assert!(Bundle::load(tmp.path()).is_err());
}
#[test]
fn bundle_manifest_selection_hides_unlisted_skills_and_rejects_missing() {
    let b =
        Bundle::load(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/skilled")).unwrap();
    assert_eq!(
        b.skills.keys().cloned().collect::<Vec<_>>(),
        vec!["report-guide"]
    );
    let (tmp, _) = fixture();
    let path = tmp.path().join("coworker/manifest.md");
    let original = std::fs::read_to_string(&path).unwrap();
    std::fs::write(
        &path,
        original.replace("skills: [report-guide]", "skills: [missing]"),
    )
    .unwrap();
    assert!(Bundle::load(tmp.path()).is_err());
    std::fs::write(
        &path,
        original.replace("skills: [report-guide]", "skills: []"),
    )
    .unwrap();
    assert_eq!(Bundle::load(tmp.path()).unwrap().skills.len(), 1);
}
#[cfg(unix)]
#[test]
fn package_symlinks_and_special_files_are_rejected_before_reading() {
    use std::os::unix::fs::symlink;
    let (tmp, _) = fixture();
    let outside = tempfile::tempdir().unwrap();
    std::fs::write(outside.path().join("secret"), "synthetic marker").unwrap();
    let link = tmp.path().join("coworker/skills/report-guide/link");
    symlink(outside.path().join("secret"), &link).unwrap();
    assert!(Bundle::load(tmp.path()).is_err());
    std::fs::remove_file(link).unwrap();
    let fifo = tmp.path().join("fifo");
    let c = std::ffi::CString::new(fifo.to_str().unwrap()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(c.as_ptr(), 0o600) }, 0);
    assert!(Bundle::load(tmp.path()).is_err());
}
