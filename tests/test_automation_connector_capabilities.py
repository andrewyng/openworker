"""Automation-specific connector capability metadata."""

from coworker.connectors.setup import connector_list
from coworker.secrets import SecretStore


def test_automation_connector_capabilities_exclude_browser_and_classify_feishu(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    connectors = {row["name"]: row for row in connector_list(SecretStore())}

    assert connectors["browser"]["source_capable"] is False
    assert connectors["github"]["source_capable"] is True
    assert connectors["feishu"]["source_capable"] is False
    assert connectors["feishu"]["delivery_capable"] is True
