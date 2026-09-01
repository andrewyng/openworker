"""`_model_backend_ready` — "is the model up?", not "is a socket open?".

The scheduler asks this before it releases the automations that were missed while the box
was down. The local shim in front of the model answers /v1/models about a second after boot,
long before the engine behind it has loaded; a probe that judges on the status code alone
therefore called a dead backend ready and fired the whole catch-up batch into it. Two boots
in 2026-08 lost 13 runs that way, every one "run ended as unknown" with no output.

So the probe asks whether the model it is about to use is in the served list.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from coworker.server import manager as manager_mod


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _probe_with(monkeypatch, response, *, model="openai:ornith-1.5-35b"):
    """Run the real probe against a canned /v1/models response."""
    mgr = SimpleNamespace(
        model=model,
        secrets=SimpleNamespace(
            get=lambda k: {"base_url": "http://127.0.0.1:5002/v1"}
            if k == "provider:openai"
            else {}
        ),
        _model_provider=lambda m: (m or "").split(":", 1)[0],
    )

    class _httpx:
        @staticmethod
        def get(url, timeout=None):
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setitem(__import__("sys").modules, "httpx", _httpx)
    probe = manager_mod.SessionManager._model_backend_ready
    return asyncio.run(probe(mgr))


def test_a_shim_answering_200_with_no_models_is_not_ready(monkeypatch):
    """The exact boot condition: proxy up, engine still loading, data:[] behind a 200."""
    assert _probe_with(monkeypatch, _Resp(200, {"object": "list", "data": []})) is False


def test_the_configured_model_being_served_is_ready(monkeypatch):
    payload = {"data": [{"id": "ornith-vllm"}, {"id": "ornith-1.5-35b"}]}
    assert _probe_with(monkeypatch, _Resp(200, payload)) is True


def test_a_different_model_being_served_is_not_ready(monkeypatch):
    """A backend serving something else cannot run this automation."""
    payload = {"data": [{"id": "some-other-model"}]}
    assert _probe_with(monkeypatch, _Resp(200, payload)) is False


def test_a_5xx_is_not_ready(monkeypatch):
    assert _probe_with(monkeypatch, _Resp(503, {"data": []})) is False


def test_a_transport_failure_is_not_ready(monkeypatch):
    assert _probe_with(monkeypatch, ConnectionError("refused")) is False


def test_an_unparseable_body_is_not_ready(monkeypatch):
    """'Not yet' is the safe reading of a body we cannot understand."""
    assert _probe_with(monkeypatch, _Resp(200, ValueError("not json"))) is False
