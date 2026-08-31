"""Phase 1 gate — persona registry lifecycle (installed → enabled → surfaced + default),
plus the shipping lineup (owner calls 2026-08-21): Chat removed, Code disabled by default,
ships:false personas hidden outside internal builds (OPENWORKER_UNSHIPPED=1)."""

from __future__ import annotations

import pytest

from coworker.personas.registry import DEFAULT_PERSONA_ID, PersonaRegistry


def _reg(tmp_path) -> PersonaRegistry:
    return PersonaRegistry(state_path=tmp_path / "personas.json")


@pytest.fixture
def internal(monkeypatch):
    """Internal build: ships:false personas (teams, ops, design…) are visible."""
    monkeypatch.setenv("OPENWORKER_UNSHIPPED", "1")


def test_builtins_present(tmp_path):
    reg = _reg(tmp_path)
    assert {"code", "cowork", "ops"} <= set(reg.ids())
    assert "chat" not in reg.ids()  # removed entirely, not just disabled
    assert reg.get("ops").builtin is True
    # Ops came from a markdown manifest; Code from a builder.
    assert reg.get("ops").manifest is not None
    assert reg.get("code").manifest is None


def test_release_lineup(tmp_path, monkeypatch):
    # A release build (no flag) offers exactly OpenWorker + the security coworkers;
    # Code is listed in Settings but disabled + unsurfaced (the recovery path).
    monkeypatch.delenv("OPENWORKER_UNSHIPPED", raising=False)
    reg = _reg(tmp_path)
    assert [e["name"] for e in reg.sidebar()] == [
        "cowork", "cloud-posture", "dep-audit", "security",
    ]
    listed = {p["id"]: p for p in reg.list_all()}
    assert set(listed) == {"cowork", "code", "cloud-posture", "dep-audit", "security"}
    assert listed["code"]["enabled"] is False and listed["code"]["surfaced"] is False
    assert listed["cloud-posture"]["group"] == "security"
    assert listed["cowork"]["group"] == "general"
    # Enabling Code from Settings puts it in the picker (enable implies surface).
    reg.set_enabled("code", True)
    assert "code" in [e["name"] for e in reg.sidebar()]


def test_unshipped_hidden_unless_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENWORKER_UNSHIPPED", raising=False)
    reg = _reg(tmp_path)
    assert "swe-lead" not in {p["id"] for p in reg.list_all()}
    # Still resolvable (a session born on it keeps working)…
    assert reg.agent("swe-lead").name == "swe-lead"
    # …and an explicit user enable (made on an internal build) keeps it visible.
    reg.set_enabled("swe-lead", True)
    assert "swe-lead" in {p["id"] for p in reg.list_all()}


def test_sidebar_defaults_to_surfaced_builtins(tmp_path, internal):
    reg = _reg(tmp_path)
    sidebar = reg.sidebar()
    ids = [e["name"] for e in sidebar]
    # Built-ins ship enabled (UX-029: the coworker picker is their front door) except
    # Code (owner 2026-08-21). Installed personas remain opt-in.
    assert ids[0] == "cowork"
    # Leads surface (the user's entry to a team — "the team IS the lead"); team
    # workers never do.
    assert set(ids) == {
        "cowork", "ops", "security", "cloud-posture", "dep-audit",
        "swe-lead", "devsecops-lead", "devops-lead", "triage-lead",
    }
    assert not any(
        i in ids
        for i in (
            "swe-worker", "design-worker", "test-worker",
            "appsec-worker", "secrets-worker", "posture-worker",
            "logs-worker", "infra-worker", "change-worker",
        )
    )
    assert sidebar[0]["default"] is True
    # An explicit disable removes a builtin from the picker.
    reg.set_enabled("security", False)
    assert "security" not in [e["name"] for e in reg.sidebar()]


def test_code_ships_disabled_but_recoverable(tmp_path):
    reg = _reg(tmp_path)
    # Code ships disabled + unsurfaced (owner call 2026-08-21): OpenWorker leads the
    # launch, Code stays one checkbox away as the plain work-in-my-repo persona.
    assert reg.is_enabled("code") is False
    assert reg.is_surfaced("code") is False
    assert reg.agent("code").name == "code"  # live sessions keep resolving
    reg.set_enabled("code", True)
    assert "code" in [e["name"] for e in reg.sidebar()]


def test_chat_gone_resolves_to_default(tmp_path):
    reg = _reg(tmp_path)
    # Chat is removed outright; a stray persona=chat session id falls back to the
    # default persona instead of erroring.
    assert reg.get("chat") is None
    assert reg.agent("chat").name == reg.default_id()


def test_surface_toggle_filters_picker_but_keeps_resolvable(tmp_path, internal):
    reg = _reg(tmp_path)
    reg.set_surfaced("ops", False)
    assert "ops" not in [e["name"] for e in reg.sidebar()]
    # Still installed + still resolvable (a session already on Ops keeps working).
    assert "ops" in reg.ids()
    assert reg.agent("ops").name == "ops"
    assert any(p["id"] == "ops" and not p["surfaced"] for p in reg.list_all())


def test_disable_default_falls_back(tmp_path):
    reg = _reg(tmp_path)
    assert reg.default_id() == DEFAULT_PERSONA_ID  # cowork
    reg.set_enabled("ops", True)  # another persona must be enabled to fall back to
    reg.set_enabled("cowork", False)
    # Cowork off → default resolves to another enabled persona, not cowork.
    assert reg.default_id() != "cowork"
    # Unknown / unspecified persona falls back to the (new) default, which is enabled.
    fallback = reg.agent(None)
    assert reg.is_enabled(fallback.name)


def test_set_default_enables_and_persists(tmp_path):
    reg = _reg(tmp_path)
    reg.set_default("ops")
    assert reg.default_id() == "ops" and reg.is_enabled("ops")
    # New instance reads persisted state.
    reg2 = _reg(tmp_path)
    assert reg2.default_id() == "ops"


def test_agent_resolution(tmp_path):
    reg = _reg(tmp_path)
    assert reg.agent("ops").requires_folder is False
    assert reg.agent("code").requires_folder is True
    # Unknown id → default persona.
    assert reg.agent("does-not-exist").name == reg.default_id()


def test_list_all_carries_requires_folder(tmp_path, internal):
    # The workspace enum collapsed into the requires_folder trait
    # (workspace-scratch-design.md): Code gates a folder; scratch personas don't.
    reg = _reg(tmp_path)
    gated = {p["id"]: p["requires_folder"] for p in reg.list_all()}
    assert gated["code"] is True
    assert gated["cowork"] is False
    assert gated["ops"] is False


def test_set_unknown_persona_raises(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(KeyError):
        reg.set_enabled("ghost", False)
