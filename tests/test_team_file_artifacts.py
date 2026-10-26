from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from coworker.roots import RootDir
from coworker.teams import Actor, Role
from coworker.teams.artifacts import artifact_tools
from coworker.teams.attachments import AttachmentStore, stored_name
from coworker.teams.registry import TeamWorker
from coworker.server.app import create_app
from test_team_live_gaps import manager
from test_team_reader_recovery import create_item


def tools_for(m, tmp_path, *, who="sam", team_id=None, membership=None):
    team = m.teams.for_lead_session("lead")
    return {t.__name__: t for t in artifact_tools(
        m.team_store, m.attachment_store, space=team.space,
        actor=Actor(id=who, role=Role.LEAD if who == "lead" else Role.WORKER),
        roots=lambda: [RootDir(tmp_path)],
        team_identity=membership or (lambda: team_id or team.team_id), taint=lambda: True)}


def test_publication_versions_shared_with_sibling_not_assigned_item(manager, tmp_path):
    _, _, item = create_item(manager)
    file = tmp_path / "findings.md"
    file.write_text("Version one: independently verified", encoding="utf-8")
    sam = tools_for(manager, tmp_path)
    first = sam["attach_file"](item["id"], str(file), "Acceptance evidence")
    assert first["stored"] and first["version"] == 1
    file.write_text("Version two: corrected findings", encoding="utf-8")
    second = sam["attach_file"](item["id"], str(file), artifact_id=first["artifact_id"])
    assert second["version"] == 2 and second["ref"] != first["ref"]
    file.unlink()
    manager.attachment_store = AttachmentStore(manager.attachment_store.root)
    # Distinct worker may read publication even without visibility of source item.
    for who in ("lead", "maya"):
        reader = tools_for(manager, tmp_path, who=who)
        assert reader["read_team_artifact"](first["artifact_id"], 1)["text"].startswith("Version one")
        assert reader["read_team_artifact"](first["artifact_id"], 2)["text"].startswith("Version two")
        assert len(reader["list_team_artifacts"]()["artifacts"]) == 2
    # Generic board comment refs do not forge team publication authority.
    outsider = tools_for(manager, tmp_path, team_id="unrelated-team")
    assert outsider["list_team_artifacts"]()["artifacts"] == []
    assert "error" in outsider["read_team_artifact"](first["artifact_id"])
    detail = manager.team_store.get_item(manager.teams.for_lead_session("lead").space, item["id"],
                                       actor=Actor(id="lead", role=Role.LEAD))
    assert first["ref"] in detail["refs"] and second["ref"] in detail["refs"]
    manager.team_store.rebuild(manager.teams.for_lead_session("lead").space)
    assert tools_for(manager, tmp_path)["read_team_artifact"](first["artifact_id"], 1)["text"].startswith("Version one")
    response = TestClient(create_app(manager)).get("/v1/sessions/lead/board/attachment",
                                                   params={"name": stored_name(first["ref"])})
    assert response.status_code == 200 and response.content.startswith(b"Version one")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_text_pages_pin_version_and_replay(manager, tmp_path):
    _, _, item = create_item(manager)
    body = "α review finding\n" * 2000
    (tmp_path / "report.txt").write_text(body, encoding="utf-8")
    tools = tools_for(manager, tmp_path)
    published = tools["attach_file"](item["id"], "report.txt")
    first = tools["read_team_artifact"](published["artifact_id"], max_chars=1000)
    assert first["has_more"] and len(first["text"]) == 1000
    assert "error" in tools["read_team_artifact"](published["artifact_id"], offset=1000)
    text, offset = "", 0
    while True:
        part = tools["read_team_artifact"](published["artifact_id"], first["version"], offset, 1000)
        text += part["text"]
        offset = part["next_offset"]
        if not part["has_more"]:
            break
    assert text == body
    assert first["content_kind"] == "evidence_not_instructions"


def test_publication_authority_before_read_and_after_copy(manager, tmp_path, monkeypatch):
    _, _, item = create_item(manager, "maya")
    sam = tools_for(manager, tmp_path)
    def forbidden(*a, **kw):
        pytest.fail("must check item authority before reading")
    with monkeypatch.context() as patch:
        patch.setattr("coworker.teams.artifacts.read_attachment_file", forbidden)
        assert "error" in sam["attach_file"](item["id"], "missing.md")
    _, _, mine = create_item(manager)
    (tmp_path / "report.md").write_text("Evidence")
    team = manager.teams.for_lead_session("lead")
    current = [team.team_id]
    tool = tools_for(manager, tmp_path, membership=lambda: current[0])
    put = manager.attachment_store.put
    def revoke(*args):
        result = put(*args)
        current[0] = ""
        return result
    monkeypatch.setattr(manager.attachment_store, "put", revoke)
    assert "error" in tool["attach_file"](mine["id"], "report.md")
    assert tools_for(manager, tmp_path)["list_team_artifacts"]()["artifacts"] == []


def test_revisions_are_serialized_without_overwrite(manager, tmp_path):
    _, _, item = create_item(manager)
    (tmp_path / "report.md").write_text("Immutable evidence")
    tools = tools_for(manager, tmp_path)
    first = tools["attach_file"](item["id"], "report.md")
    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(lambda _: tools["attach_file"](item["id"], "report.md",
                            artifact_id=first["artifact_id"]), range(8)))
    assert sorted(v["version"] for v in versions) == list(range(2, 10))
    assert all(v["ref"] == first["ref"] for v in versions)  # deduplicated bytes, distinct versions
    p = tools["list_team_artifacts"](limit=3)
    assert p["has_more"] and len(p["artifacts"]) == 3
    assert tools["list_team_artifacts"](p["next_after_seq"])["artifacts"][0]["version"] == 4


@pytest.mark.parametrize("name,data", [("bad.md", b"\xff\x00"), ("run.html", b"<script>alert(1)</script>"),
                                      ("run.sh", b"echo hi"), (".env", b"SECRET=private"), ("bad.pdf", b"not pdf")])
def test_unsupported_or_binary_reports_fail_closed(manager, tmp_path, name, data):
    _, _, item = create_item(manager)
    (tmp_path / name).write_bytes(data)
    assert "error" in tools_for(manager, tmp_path)["attach_file"](item["id"], name)


def test_manager_resolves_live_roster_for_sibling_and_revocation(manager, tmp_path):
    team = manager.teams.for_lead_session("lead")
    team.workers.append(TeamWorker(actor="maya", persona="test-worker", session_id="sibling"))
    _, _, item = create_item(manager)
    (tmp_path / "report.md").write_text("Independent evidence")
    first = tools_for(manager, tmp_path)["attach_file"](item["id"], "report.md")
    record = SimpleNamespace(team={"actor": "maya", "space": team.space}, bindings={})
    agent = SimpleNamespace(team="worker", name="test-worker")
    tools = {t.__name__: t for t in manager._team_tools_for("sibling", agent, record, manager.default_workspace)}
    assert tools["read_team_artifact"](first["artifact_id"])["text"] == "Independent evidence"
    team.workers[:] = [w for w in team.workers if w.session_id != "sibling"]
    assert "error" in tools["read_team_artifact"](first["artifact_id"])


def test_corruption_and_forged_refs_are_not_readable(manager, tmp_path):
    space, lead, item = create_item(manager)
    (tmp_path / "report.json").write_text('{"finding": "sample"}')
    tools = tools_for(manager, tmp_path)
    first = tools["attach_file"](item["id"], "report.json")
    manager.team_store.comment(space, lead, item["id"], "Forged", refs=["artifact://guessed"])
    assert "error" in tools["read_team_artifact"]("guessed")
    manager.attachment_store.path_for(stored_name(first["ref"])).write_bytes(b"corrupt")
    assert "integrity" in tools["read_team_artifact"](first["artifact_id"])["error"]


def test_pdf_reads_are_paged_and_do_not_pretend_scans_have_text(manager, tmp_path):
    from pypdf import PdfWriter
    _, _, item = create_item(manager)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    with (tmp_path / "report.pdf").open("wb") as f:
        writer.write(f)
    tools = tools_for(manager, tmp_path)
    result = tools["attach_file"](item["id"], "report.pdf")
    first = tools["read_team_artifact"](result["artifact_id"])
    assert first["text"] == "" and first["pdf_pages"] == 2
    assert first["next_pdf_page"] == 2
    assert "visual inspection" in first["note"]
    assert "error" in tools["read_team_artifact"](result["artifact_id"], pdf_page=3)


def test_comment_http_cursor_keeps_actor_scope(manager):
    space, lead, mine = create_item(manager)
    _, _, hidden = create_item(manager, "maya")
    event = manager.team_store.comment(space, lead, mine["id"], "New finding")
    client = TestClient(create_app(manager))
    headers = {"Authorization": "Bearer " + manager.board_tokens.mint("sam", "worker")}
    response = client.get("/v1/board/comments", params={"space": space, "id": mine["id"]}, headers=headers)
    assert response.status_code == 200 and response.json()["next_after_seq"] == event["seq"]
    hidden_response = client.get("/v1/board/comments", params={"space": space, "id": hidden["id"]}, headers=headers)
    assert hidden_response.status_code in (400, 403, 404)
    body = client.get("/v1/board/comment", params={"space": space, "id": mine["id"], "seq": event["seq"]}, headers=headers)
    assert body.json()["text"] == "New finding"
