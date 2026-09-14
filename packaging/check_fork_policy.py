#!/usr/bin/env python3
"""Fail CI if an upstream integration erases critical fork protections."""
import base64
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://github.com/Pol-Lanski/openworker/releases/latest/download/latest.json"


def check(root=ROOT):
    config = json.loads((root / "surfaces/gui/src-tauri/tauri.conf.json").read_text())
    assert config["plugins"]["updater"]["endpoints"] == [ENDPOINT], "Updater must use only the fork channel"
    key = config["plugins"]["updater"]["pubkey"]
    if key:
        decoded = base64.b64decode(key, validate=True).decode().splitlines()
        assert len(decoded) == 2, "Expected a Tauri minisign public key"
        packet = base64.b64decode(decoded[1], validate=True)
        assert len(packet) == 42 and packet[:2] == b"Ed", "Invalid minisign public key"
        assert packet[2:10].hex() != "69ca3599693f715b", "Upstream signing key must not be trusted"
    if os.environ.get("TAURI_SIGNING_PRIVATE_KEY"):
        assert key.strip(), "Configure the fork public key before signing updater artifacts"
    registry = (root / "coworker/providers/registry.py").read_text()
    assert '"Dappnode Nexus"' in registry and '"NEXUS_API_KEY"' in registry, "Nexus descriptor/key isolation missing"
    assert 'min-release-age=7' in (root / "surfaces/gui/.npmrc").read_text(), "npm quarantine missing"
    for workflow in ("ci.yml", "release.yml"):
        assert 'bash packaging/npm_install_safe.sh' in (root / '.github/workflows' / workflow).read_text(), "Safe npm install missing"
    assert 'codesign --verify --deep --strict' in (root / 'packaging/build_dmg.sh').read_text(), "macOS signature gate missing"


if __name__ == '__main__':
    check()
