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
connectors: true
recommended_models: [anthropic:claude-opus-4-8]
default_permission_mode: interactive
---
You are a demo coworker. Do helpful things.
"""


def test_parse_valid():
    m = parse_manifest(VALID)
    assert m.id == "demo" and m.name == "Demo Coworker"
    assert m.tools == ["files", "search", "shell", "todo"]
    assert m.family == "knowledge" and m.workspace == "deliverable"
    assert m.messaging is True and m.connectors is True
    assert m.recommended_models == ["anthropic:claude-opus-4-8"]
    assert m.needs_workspace is True
    assert m.system_prompt.startswith("You are a demo coworker")


def test_to_agent_carries_traits_and_tools(tmp_path):
    from coworker.agents.base import AgentContext
    from coworker.tools.todo import TodoList

    agent = parse_manifest(VALID).to_agent()
    assert agent.name == "demo" and agent.family == "knowledge"
    assert agent.messaging and agent.connectors
    ctx = AgentContext(workspace=tmp_path, executor=object(), todo=TodoList())
    names = {getattr(t, "__name__", "") for t in agent.build_tools(ctx)}
    assert {"read_file", "grep", "run_shell", "todo_write"} <= names


def test_list_field_accepts_comma_string():
    text = VALID.replace("tools: [files, search, shell, todo]", "tools: files, search")
    assert parse_manifest(text).tools == ["files", "search"]


def test_workspace_key_is_accepted_but_derived_from_family():
    # §16 collapse: the old enum still parses (back-compat + typo detection) but behavior
    # derives from family — knowledge → scratch ("deliverable"), code → "git". A manifest
    # can no longer demand a folder gate (`project`) or opt out of a workspace (`none`).
    text = """---
id: opsy
workspace: project
tools: [files, search, shell, todo]
---
Operate things.
"""
    m = parse_manifest(text)
    assert m.workspace == "deliverable" and m.needs_workspace is True

    coded = parse_manifest(
        "---\nid: dev\nfamily: code\nworkspace: none\ntools: [git]\n---\nCode."
    )
    assert coded.workspace == "git" and coded.needs_workspace is True


@pytest.mark.parametrize(
    "text,needle",
    [
        ("no frontmatter here", "frontmatter"),
        ("---\nid: x\ntools: [files]\n", "unterminated"),
        ("---\nname: x\n---\nbody", "id"),
        ("---\nid: x\ntools: [files]\n---\n", "no body"),
        ("---\nid: x\ntools: [nope]\n---\nbody", "unknown tool"),
        ("---\nid: x\nfamily: alien\ntools: []\n---\nbody", "family"),
        ("---\nid: x\nworkspace: cloud\ntools: []\n---\nbody", "workspace"),
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
    # A persona may recommend a connector we don't ship yet — structure only, no catalog check.
    recs = parse_manifest(
        "---\nid: x\ntools: []\nrecommends:\n  - connector: not_a_real_connector\n---\nbody"
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


# -- intro + accent (the persona's own start screen and colour) ----------------------------

INTRO = """---
id: briefer
name: Briefer
family: knowledge
tools: [files]
accent: Violet
intro:
  greeting: What should I brief you on?
  lede: I read the sources, then write the brief.
  placeholder: Describe the brief…
  starters:
    - title: Brief me on a topic
      sub: Every claim carries its URL
      prompt: Research the topic and write the brief.
    - title: Post the digest to the team
      prompt: Summarize the week and post it.
      requires: [slack, github]
    - key: pinned
      title: Pick up the folder I shared
      prompt: Read the shared folder.
      requires: [folder]
---
You are a briefer.
"""


def test_parse_intro_and_accent():
    m = parse_manifest(INTRO)
    assert m.accent == "violet"  # case-normalized
    assert m.intro.greeting == "What should I brief you on?"
    assert m.intro.placeholder == "Describe the brief…"
    first, second, third = m.intro.starters
    # Keys are derived from the title on whole-word boundaries, or taken verbatim when declared.
    assert first.key == "brief-me-on-a-topic" and first.sub == "Every claim carries its URL"
    assert second.requires == ["slack", "github"] and second.sub == ""
    assert third.key == "pinned" and third.requires == ["folder"]


def test_no_intro_block_is_empty_not_an_error():
    m = parse_manifest(VALID)
    assert not m.intro and m.intro.starters == [] and m.accent == ""


def test_intro_serializes_for_the_api():
    row = parse_manifest(INTRO).intro.as_dict()
    assert row["greeting"] == "What should I brief you on?"
    assert row["starters"][1]["requires"] == ["slack", "github"]


@pytest.mark.parametrize(
    "block, message",
    [
        ("accent: chartreuse\n", "accent must be one of"),
        ("intro: nope\n", "`intro` must be a mapping"),
        ("intro:\n  starters: nope\n", "`intro.starters` must be a list"),
        ("intro:\n  starters:\n    - title: No prompt\n", "needs a `title` and a `prompt`"),
        ("intro:\n  starters:\n    - prompt: No title\n", "needs a `title` and a `prompt`"),
        ("intro:\n  starters:\n    - just a string\n", "must be a mapping"),
    ],
)
def test_invalid_presentation_fails_loudly(block, message):
    # A malformed manifest must raise, not silently ship a persona with a broken start screen.
    with pytest.raises(ManifestError, match=message):
        parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))


def test_too_many_starters_rejected():
    rows = "".join(f"    - title: Task {i}\n      prompt: Do {i}\n" for i in range(5))
    with pytest.raises(ManifestError, match="at most 4"):
        parse_manifest(VALID.replace("---\nYou are", f"intro:\n  starters:\n{rows}---\nYou are"))


# -- checkpoints (the shape of one job) ----------------------------------------------------

CHECKS = """---
id: briefer
name: Briefer
family: knowledge
tools: [files]
checkpoints:
  - label: Plan the run
    evidence: [todo_write]
  - id: sources
    label: Search the sources
    evidence: [web_search, web_fetch]
---
You are a briefer.
"""


def test_parse_checkpoints():
    m = parse_manifest(CHECKS)
    first, second = m.checkpoints
    assert first.id == "plan-the-run" and first.evidence == ["todo_write"]
    assert second.id == "sources" and second.label == "Search the sources"
    assert m.checkpoints[1].as_dict()["evidence"] == ["web_search", "web_fetch"]


def test_no_checkpoints_is_fine():
    assert parse_manifest(VALID).checkpoints == []


def test_a_checkpoint_without_evidence_is_rejected():
    # A step no tool call can satisfy stays pending forever, so a finished run reads as stuck.
    block = "checkpoints:\n  - label: Think about it\n"
    with pytest.raises(ManifestError, match="needs `evidence`"):
        parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))


def test_a_checkpoint_without_a_label_is_rejected():
    block = "checkpoints:\n  - evidence: [todo_write]\n"
    with pytest.raises(ManifestError, match="needs a `label`"):
        parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))


def test_a_checkpoint_can_gate_its_evidence_on_an_earlier_step():
    """`after` is how a step says its evidence only counts in sequence.

    `run_shell` is the most-called tool in any code run — ls, wc, sed — so on its own it is not
    evidence of verification. Ungated, Verify was satisfied in a run's first few calls, which
    made every unfinished step before it (Implement, most of all) read as deliberately skipped.
    """
    block = (
        "checkpoints:\n"
        "  - label: Implement\n"
        "    evidence: [write_file]\n"
        "  - label: Verify\n"
        "    evidence: [run_shell]\n"
        "    after: implement\n"
    )
    m = parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))
    implement, verify = m.checkpoints
    assert implement.after == "" and "after" not in implement.as_dict()
    assert verify.after == "implement"
    assert verify.as_dict()["after"] == "implement"


def test_a_gate_naming_a_later_step_is_rejected():
    # A gate that can never open pins the step pending for the whole run — the same failure the
    # empty-`evidence` rule prevents, arriving by a different door.
    block = (
        "checkpoints:\n"
        "  - label: Verify\n"
        "    evidence: [run_shell]\n"
        "    after: implement\n"
        "  - label: Implement\n"
        "    evidence: [write_file]\n"
    )
    with pytest.raises(ManifestError, match="not an earlier checkpoint"):
        parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))


def test_too_many_checkpoints_rejected():
    rows = "".join(f"  - label: Step {i}\n    evidence: [todo_write]\n" for i in range(7))
    with pytest.raises(ManifestError, match="at most 6 checkpoints"):
        parse_manifest(VALID.replace("---\nYou are", f"checkpoints:\n{rows}---\nYou are"))


# -- budgets (advisory per-run ceilings) ---------------------------------------------------

BUDGETS = """---
id: briefer
name: Briefer
family: knowledge
tools: [files]
budgets:
  - label: searches
    limit: 8
    tools: [web_search, mcp__tavily__tavily-search]
  - id: reads
    label: page reads
    limit: 10
    tools: [web_fetch]
---
You are a briefer.
"""


def test_parse_budgets():
    m = parse_manifest(BUDGETS)
    first, second = m.budgets
    assert first.id == "searches" and first.limit == 8
    assert first.tools == ["web_search", "mcp__tavily__tavily-search"]
    assert second.id == "reads" and second.label == "page reads" and second.limit == 10
    assert second.as_dict()["limit"] == 10


def test_no_budgets_is_fine():
    assert parse_manifest(VALID).budgets == []


def test_a_budget_needs_something_to_count():
    # A budget with no tools sits at 0/N forever and quietly reassures — worse than no budget.
    block = "budgets:\n  - label: searches\n    limit: 5\n"
    with pytest.raises(ManifestError, match="needs `tools`"):
        parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))


@pytest.mark.parametrize("limit", ["0", "-3"])
def test_a_nonpositive_limit_is_rejected(limit):
    # A ceiling of zero can only ever read as already exceeded.
    block = f"budgets:\n  - label: searches\n    limit: {limit}\n    tools: [web_search]\n"
    with pytest.raises(ManifestError, match="can only ever read as already exceeded"):
        parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))


def test_a_non_numeric_limit_is_rejected():
    block = "budgets:\n  - label: searches\n    limit: lots\n    tools: [web_search]\n"
    with pytest.raises(ManifestError, match="whole-number `limit`"):
        parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))


def test_a_budget_without_a_label_is_rejected():
    block = "budgets:\n  - limit: 5\n    tools: [web_search]\n"
    with pytest.raises(ManifestError, match="needs a `label`"):
        parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))


def test_too_many_budgets_rejected():
    rows = "".join(
        f"  - label: b{i}\n    limit: 3\n    tools: [web_search]\n" for i in range(5)
    )
    with pytest.raises(ManifestError, match="at most 4 budgets"):
        parse_manifest(VALID.replace("---\nYou are", f"budgets:\n{rows}---\nYou are"))


def test_a_star_budget_counts_every_call():
    # The commonest budget in the automation corpus is a TOTAL tool-call ceiling; enumerating a
    # persona's whole toolset to express it would rot as the catalog changes.
    block = "budgets:\n  - label: tool calls\n    limit: 20\n    tools: ['*']\n"
    m = parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))
    assert m.budgets[0].counts_everything and m.budgets[0].limit == 20


def test_star_cannot_be_mixed_with_named_tools():
    block = "budgets:\n  - label: calls\n    limit: 9\n    tools: ['*', web_search]\n"
    with pytest.raises(ManifestError, match="counted twice"):
        parse_manifest(VALID.replace("---\nYou are", block + "---\nYou are"))
