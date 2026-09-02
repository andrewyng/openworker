"""Phase 1 gate — persona manifest parsing + validation."""

from __future__ import annotations

import pytest

from coworker.personas.manifest import ManifestError, parse_manifest

VALID = """---
id: demo
name: Demo Coworker
icon: demo
tagline: A demo
family: knowledge
workspace: deliverable
tools: [files, search, shell, todo]
messaging: true
connectors: [github]
recommended_models: [anthropic:claude-opus-4-8]
default_permission_mode: interactive
---
You are a demo coworker. Do helpful things.
"""


def test_parse_valid():
    m = parse_manifest(VALID)
    assert m.id == "demo" and m.name == "Demo Coworker"
    assert m.tools == ["files", "search", "shell", "todo"]
    assert m.requires_folder is False and m.scheduling is True
    assert m.messaging is True and m.connectors == ("github",)
    assert m.recommended_models == ["anthropic:claude-opus-4-8"]
    assert m.system_prompt.startswith("You are a demo coworker")


def _with_connectors(value: str, extra: str = "") -> str:
    return VALID.replace("connectors: [github]", f"connectors: {value}{extra}")


def test_connector_allowlist_dedupes_and_orders():
    m = parse_manifest(_with_connectors("[github, slack, github]"))
    assert m.connectors == ("github", "slack")


def test_legacy_true_migrates_to_the_recommended_connectors():
    """Pre-allowlist manifests said `connectors: true` — author intent lives in
    `recommends`, so the grant falls back to those refs (OPE-93)."""
    text = _with_connectors(
        "true",
        "\nrecommends:\n  - connector: github\n    reason: open PRs\n    tier: core",
    )
    assert parse_manifest(text).connectors == ("github",)


def test_legacy_true_without_recommends_grants_nothing():
    """No list, no recommends → nothing. Fail closed, never 'everything'."""
    assert parse_manifest(_with_connectors("true")).connectors is False


def test_connectors_all_is_builtin_only():
    """`all` is the trust violation the allowlist exists to prevent when a SHARED
    bundle claims it — reserved for the general built-in personas."""
    text = _with_connectors("all")
    with pytest.raises(ManifestError, match="reserved for built-in"):
        parse_manifest(text)
    assert parse_manifest(text, builtin=True).connectors is True


def test_recommending_an_undeclared_connector_is_author_drift():
    text = _with_connectors(
        "[github]",
        "\nrecommends:\n  - connector: slack\n    reason: post digests\n    tier: optional",
    )
    with pytest.raises(ManifestError, match="does not declare"):
        parse_manifest(text)


def test_to_agent_carries_traits_and_tools(tmp_path):
    from coworker.agents.base import AgentContext
    from coworker.tools.todo import TodoList

    agent = parse_manifest(VALID).to_agent()
    assert agent.name == "demo" and agent.requires_folder is False
    assert agent.messaging and agent.connectors
    ctx = AgentContext(workspace=tmp_path, executor=object(), todo=TodoList())
    names = {getattr(t, "__name__", "") for t in agent.build_tools(ctx)}
    assert {"read_file", "grep", "run_shell", "todo_write"} <= names


def test_list_field_accepts_comma_string():
    text = VALID.replace("tools: [files, search, shell, todo]", "tools: files, search")
    assert parse_manifest(text).tools == ["files", "search"]


def test_legacy_family_key_maps_to_traits():
    # Legacy shim (workspace-scratch-design.md): pre-trait bundles declared
    # `family: code|knowledge` (plus a dead `workspace:` enum, ignored). `family: code`
    # maps to the folder-gated profile; explicit new keys always win.
    text = """---
id: opsy
workspace: project
tools: [files, search, shell, todo]
---
Operate things.
"""
    m = parse_manifest(text)
    assert m.requires_folder is False and m.subagents is False and m.scheduling is True

    coded = parse_manifest(
        "---\nid: dev\nfamily: code\nworkspace: none\ntools: [git]\n---\nCode."
    )
    assert coded.requires_folder and coded.subagents and not coded.scheduling

    # New keys override the shim.
    mixed = parse_manifest(
        "---\nid: dev2\nfamily: code\nrequires_folder: false\ntools: [git]\n---\nCode."
    )
    assert mixed.requires_folder is False and mixed.subagents is True


@pytest.mark.parametrize(
    "text,needle",
    [
        ("no frontmatter here", "frontmatter"),
        ("---\nid: x\ntools: [files]\n", "unterminated"),
        ("---\nname: x\n---\nbody", "id"),
        ("---\nid: x\ntools: [files]\n---\n", "no body"),
        ("---\nid: x\ntools: [nope]\n---\nbody", "unknown tool"),
        ("---\nid: x\nfamily: alien\ntools: []\n---\nbody", "family"),
        (
            "---\nid: x\ndefault_permission_mode: yolo\ntools: []\n---\nbody",
            "permission",
        ),
    ],
)
def test_invalid_manifests_rejected(text, needle):
    with pytest.raises(ManifestError) as e:
        parse_manifest(text)
    assert needle in str(e.value).lower()


def test_fallback_id_from_filename():
    m = parse_manifest("---\nname: X\ntools: []\n---\nbody", fallback_id="ops")
    assert m.id == "ops"


# Ids become directory names under the managed install area (snapshot on install, rmtree on
# uninstall), so hostile or merely unlucky ids must be rejected at parse time: `..`/slashes
# would escape the install dir; `:*?"<>|` are invalid filename chars on Windows.
@pytest.mark.parametrize(
    "bad_id",
    ["../../evil", "a/b", "a\\b", "sales:v2", "up*", "..", "A", "-lead", "x" * 65],
)
def test_unsafe_explicit_ids_rejected(bad_id):
    with pytest.raises(ManifestError) as e:
        parse_manifest(f"---\nid: {bad_id!r}\ntools: []\n---\nbody")
    assert "invalid" in str(e.value)


def test_fallback_id_is_slugified_not_rejected():
    # A filename like "My Persona.md" (no explicit id) installs as a safe slug.
    m = parse_manifest("---\nname: X\ntools: []\n---\nbody", fallback_id="My Persona")
    assert m.id == "my-persona"
    with pytest.raises(ManifestError):  # nothing salvageable in the stem
        parse_manifest("---\nname: X\ntools: []\n---\nbody", fallback_id="..")


REC = """---
id: ops
tools: []
connectors: [github]
recommends:
  - connector: github
    reason: confirm deploys
    tier: core
  - mcp: filesystem
    reason: read runbooks
---
body
"""


def test_recommends_parsed():
    recs = parse_manifest(REC).recommends
    assert [(r.kind, r.ref, r.tier) for r in recs] == [
        ("connector", "github", "core"),
        ("mcp", "filesystem", "optional"),  # tier defaults to optional
    ]
    assert recs[0].reason == "confirm deploys"


def test_recommends_not_validated_against_shipped_connectors():
    # A persona may recommend (and declare) a connector we don't ship yet — structure
    # only, no catalog check. An unshipped id simply never intersects with connected.
    recs = parse_manifest(
        "---\nid: x\ntools: []\nconnectors: [not_a_real_connector]\n"
        "recommends:\n  - connector: not_a_real_connector\n---\nbody"
    ).recommends
    assert recs[0].ref == "not_a_real_connector"


@pytest.mark.parametrize(
    "text,needle",
    [
        ("---\nid: x\ntools: []\nrecommends: nope\n---\nbody", "must be a list"),
        ("---\nid: x\ntools: []\nrecommends:\n  - reason: hi\n---\nbody", "connector"),
        (
            "---\nid: x\ntools: []\nrecommends:\n  - connector: gh\n    tier: maybe\n---\nbody",
            "tier",
        ),
    ],
)
def test_invalid_recommends_rejected(text, needle):
    with pytest.raises(ManifestError) as e:
        parse_manifest(text)
    assert needle in str(e.value).lower()
