"""Sharing v1 (OPE-7) — export/import round trip, version provenance, re-consent rules.

A coworker bundle (manifest + skills/) zips losslessly, imports through the same consent
path as every install, records provenance, and on re-install shows a "replaces vN" note —
keeping the user's enabled state unless the capability set GREW.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from coworker.personas.registry import PersonaRegistry
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import create_app
from coworker.server.manager import SessionManager

MANIFEST_V1 = """---
id: team-sec
name: Team Security Coworker
tagline: Our security playbook
family: code
version: "1"
tools: [code_files, search]
skills: [triage]
---
You review code the way our team does.
"""

MANIFEST_V2_SAME = MANIFEST_V1.replace('version: "1"', 'version: "2"')
MANIFEST_V2_GROWN = MANIFEST_V2_SAME.replace(
    "tools: [code_files, search]", "tools: [code_files, search, shell]"
)


def _bundle(tmp_path, name, manifest):
    d = tmp_path / name
    (d / "skills" / "triage").mkdir(parents=True)
    (d / "manifest.md").write_text(manifest, encoding="utf-8")
    (d / "skills" / "triage" / "SKILL.md").write_text(
        "---\nname: triage\ndescription: our triage playbook\n---\nTriage like we do.\n",
        encoding="utf-8",
    )
    return d


def _reg(tmp_path) -> PersonaRegistry:
    return PersonaRegistry(state_path=tmp_path / "state" / "personas.json")


def test_export_import_round_trip(tmp_path):
    reg = _reg(tmp_path)
    reg.install_from_dir(_bundle(tmp_path, "authored", MANIFEST_V1))
    out = tmp_path / "shared"
    out.mkdir()
    res = reg.export_persona("team-sec", out)
    assert res["ok"] is True and res["path"].endswith("team-sec-coworker-v1.zip")

    # A second registry (the teammate) imports the zip: same skills, disabled pending
    # consent, provenance recorded.
    reg2 = PersonaRegistry(state_path=tmp_path / "state2" / "personas.json")
    summaries = reg2.install_from_zip(open(res["path"], "rb").read(), "team-sec.zip")
    assert [s["id"] for s in summaries] == ["team-sec"]
    assert summaries[0]["version"] == "1"
    assert summaries[0]["replaces"] is None
    assert reg2.is_enabled("team-sec") is False
    m = reg2.get("team-sec").manifest
    from pathlib import Path

    assert (Path(m.source).parent / "skills" / "triage" / "SKILL.md").is_file()


def test_reinstall_same_capabilities_keeps_enabled(tmp_path):
    reg = _reg(tmp_path)
    reg.install_from_dir(_bundle(tmp_path, "v1", MANIFEST_V1))
    reg.set_enabled("team-sec", True)

    summaries = reg.install_from_dir(_bundle(tmp_path, "v2", MANIFEST_V2_SAME))
    rep = summaries[0]["replaces"]
    assert rep and rep["version"] == "1" and rep["capabilities_grew"] is False
    # Same-or-smaller capabilities → the user's enabled state survives the update.
    assert reg.is_enabled("team-sec") is True
    assert reg.get("team-sec").manifest.version == "2"


def test_reinstall_with_grown_capabilities_requires_reconsent(tmp_path):
    reg = _reg(tmp_path)
    reg.install_from_dir(_bundle(tmp_path, "v1", MANIFEST_V1))
    reg.set_enabled("team-sec", True)

    summaries = reg.install_from_dir(_bundle(tmp_path, "v2", MANIFEST_V2_GROWN))
    rep = summaries[0]["replaces"]
    assert rep and rep["capabilities_grew"] is True
    # New tools = a new decision — never a silent upgrade.
    assert reg.is_enabled("team-sec") is False


def test_zip_slip_and_garbage_are_rejected(tmp_path):
    import io
    import zipfile

    import pytest

    reg = _reg(tmp_path)
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../outside.md", "nope")
    with pytest.raises(Exception):
        reg.install_from_zip(evil.getvalue(), "evil.zip")
    with pytest.raises(Exception):
        reg.install_from_zip(b"not a zip", "garbage.zip")


def test_endpoints_round_trip(tmp_path, monkeypatch):
    import base64

    class P(ProviderClient):
        def complete(self, **kw):
            raise AssertionError

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(workspace=tmp_path, provider=P())
    client = TestClient(create_app(mgr))

    mgr.personas.install_from_dir(_bundle(tmp_path, "authored", MANIFEST_V1))
    out = tmp_path / "shared"
    out.mkdir()
    res = client.post("/v1/personas/team-sec/export", json={"dir": str(out)}).json()
    assert res["ok"] is True

    zip_b64 = base64.b64encode(open(res["path"], "rb").read()).decode()
    res2 = client.post(
        "/v1/personas/install", json={"zip_b64": zip_b64, "filename": "team-sec.zip"}
    ).json()
    assert res2["ok"] is True
    assert res2["consent"][0]["id"] == "team-sec"
    assert res2["consent"][0]["version"] == "1"
    # The builtins can't be exported (builders have no bundle) — clean error, not a crash.
    res3 = client.post("/v1/personas/cowork/export", json={"dir": str(out)}).json()
    assert res3["ok"] is False
