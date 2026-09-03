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
    # Binary junk with no extension hint → the catch-all names both accepted shapes.
    with pytest.raises(ValueError, match=r"\.zip or a SKILL\.md"):
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


def test_upload_mac_finder_zip_junk_stripped(store):
    """macOS Finder's Compress injects __MACOSX/._* shadows and .DS_Store — a Mac-made
    zip must install clean on Windows/Linux, with none of that staged or listed."""
    preview = store.stage_upload(
        _zip_bytes(
            {
                "greet/SKILL.md": SKILL_MD,
                "greet/notes.txt": "real resource",
                "greet/.DS_Store": "junk",
                "__MACOSX/greet/._SKILL.md": "junk",
                "__MACOSX/greet/._notes.txt": "junk",
            }
        ),
        filename="greet.zip",
    )
    assert preview["name"] == "greet"
    assert preview["files"] == ["notes.txt"]  # junk neither listed…
    saved = store.confirm_upload(preview["token"], scope="global")
    folder = Path(saved["path"])
    installed = sorted(p.name for p in folder.rglob("*"))
    assert installed == ["SKILL.md", "notes.txt"]  # …nor installed


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


# -- save_skill tool (SKILLS-SPEC §5.2 — the worker-authors door) -------------------


from coworker.skills import save_skill_tool  # noqa: E402


@pytest.fixture()
def session_dir(tmp_path):
    d = tmp_path / "session-root"
    d.mkdir()
    return d


def test_save_skill_adds_a_new_global_skill(store, session_dir):
    tool = save_skill_tool(store, allowed_dirs=[session_dir])
    result = tool(
        name="weekly-report",
        description="Monday status report",
        instructions="1. Gather updates\n2. Write the report",
    )
    assert result["ok"] and result["action"] == "added"
    skill = SkillLoader([store.global_dir]).get("weekly-report")
    assert skill.description == "Monday status report"


def test_save_skill_bundles_files_from_session_roots(store, session_dir):
    script = session_dir / "fetch_prs.py"
    script.write_text("print('prs')", encoding="utf-8")
    example = session_dir / "sub" / "example-report.md"
    example.parent.mkdir()
    example.write_text("# Example", encoding="utf-8")
    tool = save_skill_tool(store, allowed_dirs=[session_dir])
    result = tool(
        name="gh-report",
        description="report",
        instructions="Run fetch_prs.py",
        files=[str(script), "sub/example-report.md"],  # absolute AND relative both work
    )
    assert result["ok"] and sorted(result["files"]) == ["example-report.md", "fetch_prs.py"]
    folder = store.global_dir / "gh-report"
    assert (folder / "fetch_prs.py").read_text(encoding="utf-8") == "print('prs')"
    assert (folder / "example-report.md").is_file()


def test_save_skill_existing_name_updates_and_keeps_resources(store, session_dir):
    store.create(name="gh-report", description="old", instructions="old body")
    (store.global_dir / "gh-report" / "keep.txt").write_text("keep", encoding="utf-8")
    tool = save_skill_tool(store, allowed_dirs=[session_dir])
    result = tool(name="gh-report", description="new", instructions="new body")
    assert result["ok"] and result["action"] == "updated"
    skill = SkillLoader([store.global_dir]).get("gh-report")
    assert skill.description == "new" and "new body" in skill.instructions
    assert (store.global_dir / "gh-report" / "keep.txt").is_file()  # siblings preserved


def test_save_skill_refuses_files_outside_session_roots(store, session_dir, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("secret", encoding="utf-8")
    tool = save_skill_tool(store, allowed_dirs=[session_dir])
    result = tool(name="x", description="d", instructions="i", files=[str(secret)])
    assert "outside this session's folders" in result["error"]
    assert not (store.global_dir / "x").exists()  # vetting happens BEFORE any disk write


def test_save_skill_validation_errors(store, session_dir):
    tool = save_skill_tool(store, allowed_dirs=[session_dir])
    assert "description" in tool(name="x", description=" ", instructions="i")["error"]
    assert "instructions" in tool(name="x", description="d", instructions=" ")["error"]
    assert "error" in tool(name="../evil", description="d", instructions="i")
    # A bundled SKILL.md is skipped silently, never an error: the instructions argument
    # becomes SKILL.md, and models routinely try to bundle their workspace draft of it —
    # erroring cost a second approval round (live drive 2026-07-27).
    (session_dir / "SKILL.md").write_text("x", encoding="utf-8")
    result = tool(name="x", description="d", instructions="i", files=["SKILL.md"])
    assert result["ok"] and result["files"] == []
    skill_md = (store.global_dir / "x" / "SKILL.md").read_text(encoding="utf-8")
    assert "i" in skill_md and "x" != skill_md  # instructions won, draft file ignored


def test_save_skill_requires_approval_metadata(store):
    tool = save_skill_tool(store)
    meta = tool.__aisuite_tool_metadata__
    assert meta.requires_approval is True  # → EXTERNAL risk → approval card, every call
    assert tool.__coworker_schema__["function"]["name"] == "save_skill"
    required = tool.__coworker_schema__["function"]["parameters"]["required"]
    assert required == ["name", "description", "instructions"]


def test_rows_report_bundled_file_count(store):
    store.create(name="plain", description="d", instructions="i")
    store.create(name="rich", description="d", instructions="i")
    rich = store.global_dir / "rich"
    (rich / "fetch.py").write_text("x", encoding="utf-8")
    (rich / "examples").mkdir()
    (rich / "examples" / "one.md").write_text("x", encoding="utf-8")
    by_name = {r["name"]: r for r in store.rows()}
    assert by_name["plain"]["files"] == 0  # SKILL.md itself is not "bundled"
    assert by_name["rich"]["files"] == 2  # counted recursively
