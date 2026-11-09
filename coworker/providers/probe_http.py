"""One HTTP client for every probe of a local model server.

`httpx.get()` builds a new `httpx.Client` for each call, and building one loads the
system certificate store: about 2 s on Windows, paid for a plain `http://` request to
this machine that never uses TLS. Measured 2026-11-06 (OPE-250): a probe of a loopback
port nobody listens on took 2.8 s with a fresh client and 0.8 s, the timeout alone, with
a shared one; Ollama's `/api/tags` took 1.26 s against 0.07 s. The probes in
local_server.py, ollama_facts.py and the manager's liveness checks go through this one
client, built once per process. Thread-safe: `httpx.Client` is, and the probes run in a
pool. A test stands a server in with `use(httpx.Client(transport=MockTransport(...)))`.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

import httpx

_client: Optional[httpx.Client] = None
_lock = threading.Lock()


def client() -> httpx.Client:
    """The shared client, built on first use. Built outside the lock (it takes a couple of
    seconds on Windows) and kept only if nothing was put in place meanwhile, so a `warm()`
    still running never replaces a client a test or a later caller installed."""
    global _client
    if _client is not None:
        return _client
    built = httpx.Client()
    with _lock:
        if _client is None:
            _client = built
        else:
            built.close()
        return _client


def warm() -> None:
    """Build the client now, off the caller's thread, so the first probe does not pay for it."""
    threading.Thread(target=client, name="probe-http-warm", daemon=True).start()


def use(fake: Optional[httpx.Client]) -> None:
    """Tests: answer every probe from `fake` (None goes back to a real client)."""
    global _client
    with _lock:
        _client = fake


def get(url: str, *, headers: Optional[dict[str, str]] = None, timeout: Any = None) -> httpx.Response:
    return client().get(url, **_kwargs(headers, timeout))


def post(
    url: str,
    *,
    json: Any = None,
    headers: Optional[dict[str, str]] = None,
    timeout: Any = None,
) -> httpx.Response:
    return client().post(url, json=json, **_kwargs(headers, timeout))


def _kwargs(headers: Optional[dict[str, str]], timeout: Any) -> dict[str, Any]:
    # `timeout=None` to httpx means "wait forever"; a probe that gives none keeps the
    # client's default instead.
    out: dict[str, Any] = {}
    if headers:
        out["headers"] = headers
    if timeout is not None:
        out["timeout"] = timeout
    return out
