"""The readiness probe must not wait forever for a model the endpoint never serves.

The probe exists because the shim in front of the model answers /v1/models a second after
boot while the 35B behind it is still loading — releasing catch-up runs into a dead backend
(2026-08-29 and -30: 13 automations, no output). The fix for that then over-corrected: it
required the manager's DEFAULT model id to appear in the served list. On this machine the
default is `gpt-5.6-sol` and the local endpoint serves `ornith-vllm`, so the probe answered
"not ready" forever on a backend that was up.
"""

import asyncio
from types import SimpleNamespace

import pytest

from coworker.server.manager import SessionManager


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


def _probe_with(monkeypatch, *, default_model, served, status=200):
    """Drive the real probe against a fake endpoint."""
    mgr = SessionManager.__new__(SessionManager)
    mgr.model = default_model
    mgr.secrets = SimpleNamespace(
        get=lambda k: {"base_url": "http://127.0.0.1:5002/v1"}
    )
    monkeypatch.setattr(
        SessionManager, "_model_provider", lambda self, m: "openai", raising=False
    )
    payload = {"data": [{"id": i} for i in served]}
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(payload, status))
    return asyncio.run(SessionManager._model_backend_ready(mgr))


def test_ready_when_backend_serves_a_different_id(monkeypatch):
    """The regression: default model absent from the list, backend plainly up."""
    assert _probe_with(
        monkeypatch, default_model="gpt-5.6-sol", served=["ornith-vllm"]
    ) is True


def test_ready_when_the_id_does_match(monkeypatch):
    assert _probe_with(
        monkeypatch, default_model="openai:ornith-1.5-35b", served=["ornith-1.5-35b"]
    ) is True


def test_not_ready_while_the_list_is_empty(monkeypatch):
    """The original bug this probe was built for — the shim answers before the engine loads.
    Kept: an empty list must still read as 'not yet', or the fix above would undo it."""
    assert _probe_with(monkeypatch, default_model="gpt-5.6-sol", served=[]) is False


def test_not_ready_on_server_error(monkeypatch):
    assert _probe_with(
        monkeypatch, default_model="gpt-5.6-sol", served=["x"], status=503
    ) is False
