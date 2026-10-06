"""Shared pytest fixtures.

`fake_slack` boots the in-process FakeSlack harness on an ephemeral port and points the Slack
adapter at it via `SLACK_API_URL`, so the real `SlackAdapter` / `slack_bolt` stack runs
end-to-end with no network, tokens, or the Slack app console. See
`coworker.testing.fake_slack` and `platform/docs/FAKE-SLACK-SPEC.md`.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from coworker.testing.fake_slack import FakeSlack


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """EVERY test gets an isolated SecretStore/state dir. Without this, any test that builds
    a SessionManager reads the developer's real machine-global state — including their cloud
    sign-in, which made test session creation emit REAL telemetry to prod (found 2026-07-03
    as burst noise in the ocw-connect-telemetry-events table)."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "coworker-state"))
    # Universal scratch provisions a per-session dir for EVERY session — without this,
    # tests would mkdir under the developer's real ~/OpenWorker.
    monkeypatch.setenv("COWORKER_SCRATCH_BASE", str(tmp_path / "coworker-scratch"))
    monkeypatch.delenv("COWORKER_API_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _no_docker_host_network_probe(monkeypatch):
    """OpenShell's preflight asks, on a Mac, whether Docker Desktop's host networking is on,
    by starting a container. No unit test may do that; the check has its own tests."""
    from coworker.sandbox import setup_cmd
    from coworker.sandbox.providers import openshell

    monkeypatch.setattr(openshell, "host_network_problem", lambda: None)
    monkeypatch.setattr(setup_cmd, "_host_network_ok_at", 0.0)


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


@pytest.fixture(autouse=True)
def _no_cached_ollama_facts():
    """Model facts are cached per Ollama server for a minute (providers/ollama_facts.py);
    a test must never see another test's fake server."""
    from coworker.providers import ollama_facts

    ollama_facts.forget_all()
    yield
    ollama_facts.forget_all()
