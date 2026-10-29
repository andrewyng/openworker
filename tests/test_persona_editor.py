"""The visual editor uses standard persona bundles and the existing consent gate."""

import pytest
from fastapi.testclient import TestClient

from coworker.personas.registry import PersonaRegistry
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import create_app
from coworker.server.manager import SessionManager


class OfflineProvider(ProviderClient):
    def complete(self, **kwargs):
        raise AssertionError("editing a persona must not call a model")

    def capabilities(self, model):
        return ModelCapabilities()


@pytest.fixture
def editor(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path, provider=OfflineProvider())
    return TestClient(create_app(manager)), manager.personas


def definition(**changes):
    return {
        "id": "my-researcher",
        "name": "My researcher",
        "system_prompt": "Research carefully.\n\nKeep evidence.",
        "tools": ["files", "search"],
        "connectors": ["github"],
        "models": ["openai:gpt-5.5"],
        "requires_folder": False,
        "subagents": False,
        "scheduling": True,
        "default_permission_mode": "interactive",
        **changes,
    }


def test_preview_validates_without_installing(editor):
    client, registry = editor
    result = client.post(
        "/v1/persona-editor/preview", json={"definition": definition()}
    ).json()
    assert result.get("ok") is True, result
    assert result["consent"]["connectors"] == ["github"]
    assert "write_local" in result["consent"]["risk"]
    assert "Keep evidence." in result["markdown"]
    assert registry.get("my-researcher") is None


def test_save_round_trips_and_requires_consent(editor, tmp_path):
    client, registry = editor
    draft = definition(
        description="A personal researcher",
        icon="🔎",
        tagline="Evidence first",
        team="lead",
        approval_guidance="Ask before publishing.",
        version="1.2",
        recommends=[
            {"kind": "connector", "ref": "github", "reason": "Sources", "tier": "core"}
        ],
    )
    result = client.post("/v1/persona-editor/save", json={"definition": draft}).json()
    assert result.get("ok") is True, result
    assert registry.is_enabled(draft["id"]) is False
    assert registry.is_surfaced(draft["id"]) is False
    saved = client.get(f"/v1/personas/{draft['id']}/definition").json()
    assert saved["definition"] == {**saved["definition"], **draft}
    reloaded = PersonaRegistry(state_path=registry.state_path)
    assert reloaded.agent(draft["id"]).system_prompt == draft["system_prompt"]
    exported = registry.export_persona(draft["id"], tmp_path)
    assert exported["ok"] is True
    from pathlib import Path

    recipient = PersonaRegistry(state_path=tmp_path / "recipient" / "personas.json")
    recipient.install_from_zip(Path(exported["path"]).read_bytes())
    assert recipient.agent(draft["id"]).system_prompt == draft["system_prompt"]
    assert recipient.get(draft["id"]).manifest.models == draft["models"]


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "../escape"},
        {"name": " "},
        {"system_prompt": " "},
        {"tools": ["unknown-tool"]},
        {"connectors": "all"},
        {"requires_folder": "false"},
        {"models": [42]},
        {"source": "/tmp/secret"},
        {"builtin": True},
        {"skills": ["../../secret"]},
        {"recommends": [{"kind": []}]},
        {"version": "../../outside"},
    ],
)
def test_editor_rejects_invalid_definitions(editor, changes):
    client, registry = editor
    before = set(registry.ids())
    for route in ("preview", "save"):
        result = client.post(
            f"/v1/persona-editor/{route}", json={"definition": definition(**changes)}
        ).json()
        assert result.get("ok") is False, result
    assert set(registry.ids()) == before


def test_no_implicit_overwrite_or_builtin_edit(editor):
    client, _ = editor
    assert client.post(
        "/v1/persona-editor/save", json={"definition": definition()}
    ).json()["ok"]
    duplicate = client.post(
        "/v1/persona-editor/save", json={"definition": definition()}
    ).json()
    assert duplicate["ok"] is False
    for payload in (
        {"definition": definition(id="cowork")},
        {"definition": definition(id="cowork"), "editing_id": "cowork"},
        {"definition": definition(id="renamed"), "editing_id": "my-researcher"},
        {"definition": definition(), "editing_id": "missing"},
    ):
        assert (
            client.post("/v1/persona-editor/save", json=payload).json()["ok"] is False
        )


def test_edit_preserves_enable_unless_capabilities_grow(editor):
    client, registry = editor
    client.post("/v1/persona-editor/save", json={"definition": definition()})
    registry.set_enabled("my-researcher", True)
    payload = {
        "editing_id": "my-researcher",
        "definition": definition(system_prompt="New prompt"),
    }
    assert client.post("/v1/persona-editor/save", json=payload).json()["ok"]
    assert registry.is_enabled("my-researcher") is True
    payload["definition"]["tools"].append("shell")
    result = client.post("/v1/persona-editor/save", json=payload).json()
    assert result["consent"][0]["replaces"]["capabilities_grew"] is True
    assert registry.is_enabled("my-researcher") is False


def test_template_copy_keeps_bundled_skills(editor, tmp_path):
    client, registry = editor
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.md").write_text(
        "---\nid: tutor-template\nname: Tutor\ntools: [files]\nskills: [tutoring]\n---\nTeach carefully."
    )
    skill = bundle / "skills" / "tutoring"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: tutoring\ndescription: Teach\n---\nExplain."
    )
    registry.install_from_dir(bundle)
    result = client.post(
        "/v1/persona-editor/save",
        json={
            "template_id": "tutor-template",
            "definition": definition(skills=["tutoring"]),
        },
    ).json()
    assert result.get("ok") is True, result
    assert (
        registry.installed_dir / "my-researcher" / "skills" / "tutoring" / "SKILL.md"
    ).is_file()
    assert registry.get("tutor-template").manifest.system_prompt == "Teach carefully."

    # Empty skill selections must not turn into the loader's legacy "all skills"
    # behavior, including when editing a snapshot that used to carry skills.
    result = client.post(
        "/v1/persona-editor/save",
        json={
            "editing_id": "my-researcher",
            "definition": definition(skills=[]),
        },
    ).json()
    assert result["ok"] is True
    manager = client.app.state.manager
    assert "tutoring" not in manager.effective_skill_names(
        "trial", agent="my-researcher"
    )


@pytest.mark.parametrize("trait", ["subagents", "scheduling"])
def test_editing_execution_traits_requires_consent(editor, trait):
    client, registry = editor
    draft = definition(subagents=False, scheduling=False)
    client.post("/v1/persona-editor/save", json={"definition": draft})
    registry.set_enabled(draft["id"], True)
    draft[trait] = True
    result = client.post(
        "/v1/persona-editor/save",
        json={
            "editing_id": draft["id"],
            "definition": draft,
        },
    ).json()
    assert result["consent"][0]["replaces"]["capabilities_grew"] is True
    assert registry.is_enabled(draft["id"]) is False


def test_editor_catalog_and_core_templates(editor):
    client, registry = editor
    options = client.get("/v1/persona-editor").json()
    assert {c["id"] for c in options["capabilities"]} >= {"files", "shell", "git"}
    assert any(c["id"] == "github" for c in options["connectors"])
    result = client.get("/v1/personas/cowork/definition").json()
    assert (
        result["definition"]["system_prompt"] == registry.agent("cowork").system_prompt
    )
    assert isinstance(result["definition"]["connectors"], list)
    result["definition"].update(id="my-cowork", name="My coworker")
    saved = client.post(
        "/v1/persona-editor/save",
        json={
            "template_id": "cowork",
            "definition": result["definition"],
        },
    ).json()
    assert saved["ok"] is True


def test_switching_from_scoped_mcp_to_inherited_mcp_requires_consent(editor):
    client, registry = editor
    draft = definition(mcp=["documents"])
    assert client.post("/v1/persona-editor/save", json={"definition": draft}).json()[
        "ok"
    ]
    registry.set_enabled(draft["id"], True)
    draft["mcp"] = []  # Empty means all enabled machine servers, not no access.
    result = client.post(
        "/v1/persona-editor/save",
        json={
            "editing_id": draft["id"],
            "definition": draft,
        },
    ).json()
    assert result["consent"][0]["replaces"]["capabilities_grew"] is True
    assert registry.is_enabled(draft["id"]) is False


def test_every_builtin_can_be_used_as_an_editor_template(editor):
    client, registry = editor
    for persona_id in registry.ids():
        draft = client.get(f"/v1/personas/{persona_id}/definition").json()["definition"]
        source_prompt = registry.agent(persona_id).system_prompt
        draft["id"] = persona_id + "-custom"
        result = client.post(
            "/v1/persona-editor/save",
            json={
                "template_id": persona_id,
                "definition": draft,
            },
        ).json()
        assert result.get("ok") is True, (persona_id, result)
        assert registry.agent(draft["id"]).system_prompt == source_prompt.strip()
