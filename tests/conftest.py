"""Shared pytest fixtures.

`fake_slack` boots the in-process FakeSlack harness on an ephemeral port and points the Slack
adapter at it via `SLACK_API_URL`, so the real `SlackAdapter` / `slack_bolt` stack runs
end-to-end with no network, tokens, or the Slack app console. See
`coworker.testing.fake_slack` and `platform/docs/FAKE-SLACK-SPEC.md`.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from coworker import secrets as secrets_module
from coworker.testing.fake_slack import FakeSlack


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """EVERY test gets an isolated SecretStore/state dir. Without this, any test that builds
    a SessionManager reads the developer's real machine-global state — including their cloud
    sign-in, which made test session creation emit REAL telemetry to prod (found 2026-07-03
    as burst noise in the ocw-connect-telemetry-events table)."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "coworker-state"))
    monkeypatch.delenv("COWORKER_API_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _fake_keyring(monkeypatch):
    """EVERY test gets an in-memory fake OS keychain for SecretStore's wrapping key. Without
    this, SecretStore falls through to the developer's real OS keychain (Keychain-unlock
    prompts on macOS) or, on CI runners with no Secret Service, to the file-key fallback path —
    either way tests would be non-hermetic and could leave stray entries in a real keychain.
    Tests that specifically want to exercise the fallback path monkeypatch
    `secrets_module.keyring = None` themselves."""
    store: dict[tuple[str, str], str] = {}

    def _get_password(service: str, username: str) -> str | None:
        return store.get((service, username))

    def _set_password(service: str, username: str, password: str) -> None:
        store[(service, username)] = password

    if secrets_module.keyring is not None:
        monkeypatch.setattr(secrets_module.keyring, "get_password", _get_password)
        monkeypatch.setattr(secrets_module.keyring, "set_password", _set_password)


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
