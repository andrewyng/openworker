"""SKILLS-SPEC §4.6 — SkillStore: folder-backed CRUD, parsing edges, staged uploads.

Scope = folder location (folder-is-truth). These tests pin the store's safety rails:
skill names become folder names (traversal guards), uploads are staged and previewed
before anything lands in a scope dir, and disable state is personal (settings JSON,
never a marker committed with a project folder).
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from coworker.skills import SkillLoader, SkillStore, validate_name


@pytest.fixture()
def store(tmp_path):
    return SkillStore(global_dir=tmp_path / "global-skills")


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    return ws


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


SKILL_MD = "---\nname: greet\ndescription: says hello\n---\n\nSay hello warmly.\n"


# -- create ----------------------------------------------------------------------


def test_create_global_roundtrip(store):
    created = store.create(
        name="weekly-report",
        description="Monday status report",
        instructions="1. Gather updates\n2. Write the report",
    )
    assert created["scope"] == "global"
    loader = SkillLoader([store.global_dir])
    skill = loader.get("weekly-report")
    assert skill.description == "Monday status report"
    assert "Gather updates" in skill.instructions


def test_create_project_scoped(store, workspace):
    store.create(
        name="release-checklist",
        description="repo release steps",
        instructions="Run the checklist.",
        scope="project",
        workspace=workspace,
    )
    md = workspace / ".coworker" / "skills" / "release-checklist" / "SKILL.md"
    assert md.is_file()


def test_create_duplicate_rejected(store):
    store.create(name="dup", description="", instructions="x")
    with pytest.raises(ValueError, match="already exists"):
        store.create(name="dup", description="", instructions="y")


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "a" * 65, "../evil", "a/b", "a\\b", ".hidden", "café"],
)
def test_invalid_names_rejected(bad):
    with pytest.raises(ValueError):
        validate_name(bad)


def test_blank_instructions_rejected(store):
    with pytest.raises(ValueError, match="instructions"):
        store.create(name="empty", description="d", instructions="   ")


# -- update / delete / move --------------------------------------------------------


def test_update_preserves_resources(store):
    store.create(name="tpl", description="v1", instructions="old body")
    extra = store.global_dir / "tpl" / "template.txt"
    extra.write_text("keep me", encoding="utf-8")
    store.update("tpl", instructions="new body")
    loader = SkillLoader([store.global_dir])
    assert loader.get("tpl").instructions == "new body"
    assert loader.get("tpl").description == "v1"  # untouched field survives
    assert extra.read_text(encoding="utf-8") == "keep me"


def test_delete_and_unknown(store):
    store.create(name="gone", description="", instructions="x")
    store.delete("gone")
    assert not (store.global_dir / "gone").exists()
    with pytest.raises(ValueError, match="Unknown skill"):
        store.delete("gone")


def test_delete_symlinked_folder_not_followed(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    store.global_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, store.global_dir / "greet", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/user")
    # Either refused (escape guard) or unlinked in place — the target must survive.
    try:
        store.delete("greet")
    except ValueError:
        pass
    assert (outside / "SKILL.md").is_file()


def test_move_roundtrip(store, workspace):
    store.create(name="mover", description="", instructions="x")
    moved = store.move("mover", to_scope="project", workspace=workspace)
    assert moved["scope"] == "project"
    assert (workspace / ".coworker" / "skills" / "mover" / "SKILL.md").is_file()
    assert not (store.global_dir / "mover").exists()
    store.move("mover", to_scope="global", workspace=workspace)
    assert (store.global_dir / "mover" / "SKILL.md").is_file()


def test_move_collision_leaves_source(store, workspace):
    store.create(name="both", description="global copy", instructions="g")
    store.create(
        name="both",
        description="project copy",
        instructions="p",
        scope="project",
        workspace=workspace,
    )
    with pytest.raises(ValueError, match="already exists"):
        store.move("both", to_scope="global", workspace=workspace)
    # most-local find() → the project copy was the move source and it survives
    assert (workspace / ".coworker" / "skills" / "both" / "SKILL.md").is_file()


# -- parsing edges (null/malformed input never crashes) -----------------------------


def _manual_skill(base: Path, folder: str, text: str) -> None:
    d = base / folder
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")


def test_no_frontmatter_falls_back_to_folder_name(store):
    _manual_skill(store.global_dir, "bare", "Just instructions, no frontmatter.")
    rows = store.rows()
    assert rows[0]["name"] == "bare"
    assert rows[0]["description"] == ""


def test_unterminated_frontmatter_no_crash(store):
    _manual_skill(store.global_dir, "broken", "---\nname: broken\nno closing fence")
    rows = store.rows()
    assert rows[0]["name"] == "broken"


def test_empty_skill_md_no_crash(store):
    _manual_skill(store.global_dir, "hollow", "")
    rows = store.rows()
    assert rows[0]["name"] == "hollow"
    assert rows[0]["enabled"] is True


def test_unicode_content_and_crlf_roundtrip(store):
    _manual_skill(
        store.global_dir,
        "emoji",
        "---\r\nname: emoji\r\ndescription: says 你好 🎉\r\n---\r\n\r\nGreet with 🎉.\r\n",
    )
    loader = SkillLoader([store.global_dir])
    skill = loader.get("emoji")
    assert "🎉" in skill.description
    assert "你好" in skill.description


def test_frontmatter_name_wins_and_keys_collisions(store, workspace):
    store.create(name="brand", description="global copy", instructions="g")
    _manual_skill(
        workspace / ".coworker" / "skills",
        "other-folder",
        "---\nname: brand\ndescription: project copy\n---\nbody",
    )
    rows = store.rows(workspace)
    brand = [r for r in rows if r["name"] == "brand"]
    assert len(brand) == 1  # one row per name, not per folder
    assert brand[0]["scope"] == "project"  # project copy shadows global


# -- uploads -----------------------------------------------------------------------


def test_upload_zip_at_root_and_nested(store):
    for entries in (
        {"SKILL.md": SKILL_MD},
        {"greet/SKILL.md": SKILL_MD, "greet/notes.txt": "extra"},
    ):
        preview = store.stage_upload(_zip_bytes(entries))
        assert preview["name"] == "greet"
        assert preview["description"] == "says hello"
        store.discard_upload(preview["token"])


def test_upload_without_skill_md_rejected(store):
    with pytest.raises(ValueError, match="SKILL.md"):
        store.stage_upload(_zip_bytes({"readme.txt": "not a skill"}))
    # A broken file that CLAIMS to be an archive fails as an archive, not as markdown.
    with pytest.raises(ValueError, match="zip"):
        store.stage_upload(b"garbage bytes", filename="broken.zip")
    with pytest.raises(ValueError, match="zip"):
        store.stage_upload(b"garbage bytes", filename="broken.skill")
    # Binary junk with no extension hint → the catch-all message names all three shapes.
    with pytest.raises(ValueError, match=r"\.zip, \.skill, or SKILL\.md"):
        store.stage_upload(b"\xff\xfe\x00\x01binary junk")


def test_upload_bare_md_with_frontmatter(store):
    preview = store.stage_upload(SKILL_MD.encode(), filename="greet.md")
    assert preview["name"] == "greet"
    assert preview["files"] == []
    saved = store.confirm_upload(preview["token"], scope="global")
    assert saved["name"] == "greet"
    assert store.rows()[0]["source"] == "uploaded"


def test_upload_bare_md_without_name_rejected(store):
    with pytest.raises(ValueError, match="frontmatter"):
        store.stage_upload(b"Just instructions, no frontmatter.", filename="notes.md")


def test_upload_dot_skill_is_a_zip_alias(store):
    preview = store.stage_upload(
        _zip_bytes({"greet/SKILL.md": SKILL_MD}), filename="greet.skill"
    )
    assert preview["name"] == "greet"
    store.discard_upload(preview["token"])


def test_upload_zip_slip_rejected(store, tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        store.stage_upload(_zip_bytes({"../evil/SKILL.md": SKILL_MD}))
    assert not (tmp_path / "evil").exists()


def test_upload_confirm_saves_previewed_content(store):
    preview = store.stage_upload(
        _zip_bytes({"greet/SKILL.md": SKILL_MD, "greet/notes.txt": "extra"})
    )
    saved = store.confirm_upload(preview["token"], scope="global")
    assert saved["name"] == "greet"
    loader = SkillLoader([store.global_dir])
    assert loader.get("greet").description == preview["description"]
    assert (store.global_dir / "greet" / "notes.txt").is_file()
    rows = store.rows()
    assert rows[0]["source"] == "uploaded"  # provenance stamped (SKILLS-SPEC v2 hook)
    with pytest.raises(ValueError, match="expired"):
        store.confirm_upload(preview["token"])  # token is one-shot


# -- disable state -------------------------------------------------------------------


def test_disable_persists_across_reload(store, monkeypatch, tmp_path):
    store.create(name="sleepy", description="", instructions="x")
    store.set_enabled("sleepy", False)
    reloaded = SkillStore(global_dir=store.global_dir)
    assert "sleepy" in reloaded.disabled_names()
    assert reloaded.rows()[0]["enabled"] is False
    reloaded.set_enabled("sleepy", True)
    assert reloaded.rows()[0]["enabled"] is True


def test_corrupt_settings_json_treated_as_empty(store):
    store._settings_path.parent.mkdir(parents=True, exist_ok=True)
    store._settings_path.write_text("{not json", encoding="utf-8")
    assert store.disabled_names() == set()
    store.set_enabled("x", False)  # recovers by rewriting the file
    assert store.disabled_names() == {"x"}
