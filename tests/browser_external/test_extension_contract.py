from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from coworker.browser_external import PROTOCOL_VERSION, SUPPORTED_COMMANDS


ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "browser-extension"


def test_manifest_is_mv3_with_stable_native_messaging_identity() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text())

    assert manifest["manifest_version"] == 3
    assert manifest["background"] == {"service_worker": "service-worker.js"}
    assert {"activeTab", "tabs", "storage", "debugger", "nativeMessaging"}.issubset(
        manifest["permissions"]
    )
    assert "host_permissions" not in manifest

    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    extension_id = "".join(chr(ord("a") + int(character, 16)) for character in digest)
    assert extension_id == "djnbhkmnbmjobnphflaopcpfkifbgekl"


def test_extension_and_bridge_protocol_versions_and_commands_match() -> None:
    worker = (EXTENSION / "service-worker.js").read_text()
    security = (EXTENSION / "security.js").read_text()

    assert f"const PROTOCOL_VERSION = {PROTOCOL_VERSION};" in worker
    assert 'importScripts("security.js")' in worker
    assert "value_state" in security
    assert "BROWSER_ACTION_OUTCOME_UNKNOWN" in worker
    for command in SUPPORTED_COMMANDS:
        assert f'case "{command}"' in worker


def test_remote_protocol_has_no_attach_or_arbitrary_cdp_command() -> None:
    assert "attach" not in SUPPORTED_COMMANDS
    assert "cdp" not in SUPPORTED_COMMANDS

    worker = (EXTENSION / "service-worker.js").read_text()
    # The one debugger attachment site belongs to the popup-only CLAIM_TAB
    # message path; the polled executeCommand allowlist has no attach branch.
    assert worker.count("chrome.debugger.attach(") == 1
    execute_section = worker.split("async function executeCommand", 1)[1]
    assert 'case "attach"' not in execute_section
    assert 'case "cdp"' not in execute_section
