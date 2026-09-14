"""Regression tests for keyless macOS release-build policy."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


OVERLAY_SCRIPT = (
    Path(__file__).resolve().parents[1] / "packaging" / "tauri_macos_overlay.sh"
)


@pytest.mark.parametrize(
    ("has_identity", "has_updater_key", "expected"),
    [
        (False, False, {"bundle": {"macOS": {"signingIdentity": "-"}}}),
        (
            False,
            True,
            {
                "bundle": {
                    "createUpdaterArtifacts": True,
                    "macOS": {"signingIdentity": "-"},
                }
            },
        ),
        (True, False, None),
        (True, True, {"bundle": {"createUpdaterArtifacts": True}}),
    ],
)
def test_tauri_macos_overlay_matrix(has_identity, has_updater_key, expected):
    env = os.environ.copy()
    env.pop("APPLE_SIGNING_IDENTITY", None)
    env.pop("TAURI_SIGNING_PRIVATE_KEY", None)
    if has_identity:
        env["APPLE_SIGNING_IDENTITY"] = "Developer ID Application: Test"
    if has_updater_key:
        env["TAURI_SIGNING_PRIVATE_KEY"] = "test-updater-key"

    result = subprocess.run(
        ["bash", str(OVERLAY_SCRIPT)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    actual = json.loads(result.stdout) if result.stdout else None
    assert actual == expected
