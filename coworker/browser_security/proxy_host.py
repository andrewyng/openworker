"""Thread-hosted lifecycle for per-conversation fail-closed browser proxies."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterable
from typing import Any

from .destination import DestinationPolicy, is_explicit_local_origin
from .proxy import FailClosedLoopbackProxy


class BrowserProxyHost:
    """Own one authenticated egress proxy per active browser conversation.

    Browser tools execute on worker threads while the proxy itself is asyncio based.
    This host keeps proxy I/O on one dedicated event-loop thread and exposes a small
    synchronous API to the session manager.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()
        self._proxies: dict[str, FailClosedLoopbackProxy] = {}
        self._closed = False

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._closed:
                raise RuntimeError("Browser proxy host is closed")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="openworker-browser-proxy",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("Browser proxy host startup timed out")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def _call(self, coroutine: Any, *, timeout: float = 20) -> Any:
        self._ensure_started()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=timeout)

    def create_session(
        self,
        session_id: str,
        *,
        local_origin_grants: Iterable[str] = (),
    ) -> dict[str, str]:
        endpoint = self._call(
            self._create_session(session_id, tuple(local_origin_grants))
        )
        return {
            "server": endpoint.proxy_url,
            "username": endpoint.username,
            "password": endpoint.token,
        }

    async def _create_session(
        self, session_id: str, grants: tuple[str, ...]
    ) -> Any:
        grants = tuple(
            grant for grant in grants if is_explicit_local_origin(grant)
        )
        existing = self._proxies.get(session_id)
        if existing is not None:
            for grant in grants:
                existing.grant_local_origin(grant)
            return existing.endpoint
        proxy = FailClosedLoopbackProxy(
            DestinationPolicy(local_origin_grants=grants)
        )
        endpoint = await proxy.start()
        self._proxies[session_id] = proxy
        return endpoint

    def grant_local_origin(self, session_id: str, url: str) -> None:
        self._call(self._grant_local_origin(session_id, url))

    async def _grant_local_origin(self, session_id: str, url: str) -> None:
        proxy = self._proxies.get(session_id)
        if proxy is None:
            raise RuntimeError("Browser proxy session does not exist")
        if is_explicit_local_origin(url):
            proxy.grant_local_origin(url)

    def close_session(self, session_id: str) -> None:
        if self._thread is None or self._closed:
            return
        self._call(self._close_session(session_id))

    async def _close_session(self, session_id: str) -> None:
        proxy = self._proxies.pop(session_id, None)
        if proxy is not None:
            await proxy.stop()

    def close(self) -> None:
        with self._start_lock:
            if self._closed:
                return
            if self._thread is None:
                self._closed = True
                return
        try:
            self._call(self._close_all())
        finally:
            self._closed = True
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=10)

    async def _close_all(self) -> None:
        proxies = list(self._proxies.values())
        self._proxies.clear()
        if proxies:
            await asyncio.gather(
                *(proxy.stop() for proxy in proxies),
                return_exceptions=True,
            )

    def __enter__(self) -> "BrowserProxyHost":
        self._ensure_started()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
