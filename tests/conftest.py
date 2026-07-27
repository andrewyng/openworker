"""Shared pytest fixtures.

`fake_slack` boots the in-process FakeSlack harness on an ephemeral port and points the Slack
adapter at it via `SLACK_API_URL`, so the real `SlackAdapter` / `slack_bolt` stack runs
end-to-end with no network, tokens, or the Slack app console. See
`coworker.testing.fake_slack` and `platform/docs/FAKE-SLACK-SPEC.md`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from coworker.testing.fake_slack import FakeSlack


def assert_user_only(path: Path) -> None:
    """Assert `path` is readable by the current user alone, per the platform's own model.

    POSIX says this with mode bits. Windows has no such bits: `os.stat` derives `st_mode`
    from the read-only attribute only, so every writable file reports 0o666 and a
    `st_mode & 0o777 == 0o600` check can never pass there, not even for a file that
    `secrets._restrict_to_user` has correctly locked down with `icacls`. Assert the ACL
    instead, mirroring how the protection is applied in the first place.
    """
    if sys.platform == "win32":
        acl = subprocess.run(
            ["icacls", str(path)], capture_output=True, text=True
        ).stdout
        # `/inheritance:r` strips inherited ACEs; their absence is what proves the file
        # was restricted rather than left to inherit SYSTEM/Administrators from its parent.
        assert "(I)" not in acl, f"{path} still carries inherited ACEs:\n{acl}"
        for principal in ("NT AUTHORITY\\SYSTEM", "BUILTIN\\Administrators"):
            assert principal not in acl, f"{path} grants {principal}:\n{acl}"
    else:
        assert (path.stat().st_mode & 0o777) == 0o600


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """EVERY test gets an isolated SecretStore/state dir. Without this, any test that builds
    a SessionManager reads the developer's real machine-global state — including their cloud
    sign-in, which made test session creation emit REAL telemetry to prod (found 2026-07-03
    as burst noise in the ocw-connect-telemetry-events table)."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "coworker-state"))
    monkeypatch.delenv("COWORKER_API_TOKEN", raising=False)


@pytest_asyncio.fixture
async def fake_slack(monkeypatch):
    """A running FakeSlack control object; `SLACK_API_URL` is set to it for the test's duration."""
    fake = FakeSlack()
    await fake.start()
    monkeypatch.setenv("SLACK_API_URL", fake.api_url)
    try:
        yield fake
    finally:
        await fake.stop()
