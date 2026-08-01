"""Thread-safe local bridge for the Chrome companion extension.

This module deliberately has no FastAPI dependency.  The desktop server can
wire its methods to HTTP while browser tools use the internal command API.
Secrets are only returned at creation/exchange time and are stored as hashes.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .protocol import (
    PROTOCOL_VERSION,
    ProtocolValidationError,
    targeted_tab_id,
    validate_command,
    validate_error_payload,
    validate_event,
    validate_result_payload,
)


class ExternalBrowserBridgeError(RuntimeError):
    """Base error with a stable code suitable for an HTTP/API adapter."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class PairingError(ExternalBrowserBridgeError):
    pass


class AuthenticationError(ExternalBrowserBridgeError):
    pass


class SessionNotFound(ExternalBrowserBridgeError):
    pass


class TabNotClaimed(ExternalBrowserBridgeError):
    pass


class CommandNotFound(ExternalBrowserBridgeError):
    pass


@dataclass(frozen=True)
class PairingChallenge:
    pairing_id: str
    expires_at: float
    browser: str
    code: str = field(repr=False)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "pairing_id": self.pairing_id,
            "pairing_code": self.code,
            "expires_at": self.expires_at,
            "browser": self.browser,
            "protocol_version": PROTOCOL_VERSION,
        }


@dataclass(frozen=True)
class PairedClient:
    session_id: str
    browser: str
    created_at: float
    session_token: str = field(repr=False)

    def to_exchange_dict(self, *, poll_timeout_seconds: float) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_token": self.session_token,
            "browser": self.browser,
            "protocol_version": PROTOCOL_VERSION,
            "poll_timeout_seconds": poll_timeout_seconds,
        }


@dataclass(frozen=True)
class CommandTicket:
    session_id: str
    request_id: str
    command: str
    created_at: float


@dataclass(frozen=True)
class BridgeResult:
    session_id: str
    request_id: str
    ok: bool
    completed_at: float
    payload: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "request_id": self.request_id,
            "ok": self.ok,
            "completed_at": self.completed_at,
        }
        if self.ok:
            value["result"] = self.payload or {}
        else:
            value["error"] = self.error or {
                "code": "UNKNOWN_ERROR",
                "message": "The extension command failed",
            }
        return value


@dataclass
class _PairingRecord:
    pairing_id: str
    code_hash: str
    browser: str
    expires_mono: float
    expires_wall: float


@dataclass
class _CommandRecord:
    request_id: str
    command: str
    params: dict[str, Any]
    created_wall: float
    created_mono: float
    state: str = "queued"
    attempts: int = 0
    leased_mono: float | None = None
    result: BridgeResult | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "command": self.command,
            "params": self.params,
            "attempt": self.attempts,
            "created_at": self.created_wall,
            "protocol_version": PROTOCOL_VERSION,
        }


@dataclass
class _SessionRecord:
    session_id: str
    token_hash: str
    browser: str
    client: dict[str, Any]
    created_wall: float
    last_seen_mono: float
    connected: bool = True
    disconnect_reason: str | None = None
    claimed_tab_ids: set[int] = field(default_factory=set)
    commands: dict[str, _CommandRecord] = field(default_factory=dict)
    order: deque[str] = field(default_factory=deque)
    events: deque[dict[str, Any]] = field(default_factory=deque)


class ExternalBrowserBridge:
    """Pair extensions and relay a constrained set of browser commands.

    Read-only delivery is at-least-once until a result is submitted. Mutating
    commands are leased exactly once: if their acknowledgement is lost, the
    bridge reports an unknown outcome instead of risking a duplicate click,
    fill, keypress, or scroll. Every command has an opaque request ID, and tool
    callers must target a tab the extension explicitly reported as shared.
    """

    _CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    _VALID_BROWSERS = frozenset({"chrome"})
    _MUTATING_COMMANDS = frozenset({"click", "fill", "keypress", "scroll"})

    def __init__(
        self,
        *,
        pairing_ttl_seconds: float = 300,
        session_idle_seconds: float = 24 * 60 * 60,
        poll_timeout_seconds: float = 25,
        command_lease_seconds: float = 30,
        max_delivery_attempts: int = 3,
        max_pending_commands: int = 128,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if pairing_ttl_seconds <= 0 or session_idle_seconds <= 0:
            raise ValueError("pairing and session TTLs must be positive")
        if poll_timeout_seconds <= 0 or command_lease_seconds <= 0:
            raise ValueError("poll and lease timeouts must be positive")
        if max_delivery_attempts < 1 or max_pending_commands < 1:
            raise ValueError("delivery and queue limits must be positive")
        self._pairing_ttl = float(pairing_ttl_seconds)
        self._session_idle = float(session_idle_seconds)
        self._poll_timeout = float(poll_timeout_seconds)
        self._lease_seconds = float(command_lease_seconds)
        self._max_attempts = int(max_delivery_attempts)
        self._max_pending = int(max_pending_commands)
        self._clock = clock
        self._wall_clock = wall_clock
        self._condition = threading.Condition(threading.RLock())
        self._pairings: dict[str, _PairingRecord] = {}
        self._sessions: dict[str, _SessionRecord] = {}
        self._tokens: dict[str, str] = {}

    @property
    def recommended_poll_timeout_seconds(self) -> float:
        return self._poll_timeout

    @staticmethod
    def _digest(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    @classmethod
    def _normalize_code(cls, code: str) -> str:
        if not isinstance(code, str):
            return ""
        return "".join(character for character in code.upper() if character.isalnum())

    def _new_pairing_code(self) -> str:
        # 50 bits of entropy, grouped only for human readability.
        raw = "".join(secrets.choice(self._CODE_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"

    def create_pairing_code(
        self,
        *,
        browser: str = "chrome",
        ttl_seconds: float | None = None,
    ) -> PairingChallenge:
        normalized_browser = browser.strip().lower()
        if normalized_browser not in self._VALID_BROWSERS:
            raise PairingError(
                "browser must be chrome", code="UNSUPPORTED_BROWSER"
            )
        ttl = self._pairing_ttl if ttl_seconds is None else float(ttl_seconds)
        if ttl <= 0 or ttl > 15 * 60:
            raise PairingError(
                "pairing code lifetime must be between 0 and 900 seconds",
                code="INVALID_PAIRING_TTL",
            )
        now_mono = self._clock()
        now_wall = self._wall_clock()
        code = self._new_pairing_code()
        normalized_code = self._normalize_code(code)
        pairing_id = secrets.token_urlsafe(12)
        record = _PairingRecord(
            pairing_id=pairing_id,
            code_hash=self._digest(normalized_code),
            browser=normalized_browser,
            expires_mono=now_mono + ttl,
            expires_wall=now_wall + ttl,
        )
        with self._condition:
            self._cleanup_locked(now_mono)
            self._pairings[record.code_hash] = record
        return PairingChallenge(
            pairing_id=pairing_id,
            code=code,
            expires_at=record.expires_wall,
            browser=normalized_browser,
        )

    def exchange_pairing_code(
        self,
        code: str,
        *,
        client: Mapping[str, Any] | None = None,
    ) -> PairedClient:
        normalized = self._normalize_code(code)
        if len(normalized) != 10:
            raise PairingError("pairing code is invalid or expired", code="INVALID_PAIRING_CODE")
        now_mono = self._clock()
        now_wall = self._wall_clock()
        with self._condition:
            self._cleanup_locked(now_mono)
            record = self._pairings.pop(self._digest(normalized), None)
            if record is None or record.expires_mono <= now_mono:
                raise PairingError(
                    "pairing code is invalid or expired", code="INVALID_PAIRING_CODE"
                )
            client_data = self._validate_client(client or {}, expected_browser=record.browser)
            token = secrets.token_urlsafe(32)
            token_hash = self._digest(token)
            session_id = secrets.token_urlsafe(18)
            session = _SessionRecord(
                session_id=session_id,
                token_hash=token_hash,
                browser=record.browser,
                client=client_data,
                created_wall=now_wall,
                last_seen_mono=now_mono,
            )
            self._sessions[session_id] = session
            self._tokens[token_hash] = session_id
            self._condition.notify_all()
        return PairedClient(
            session_id=session_id,
            session_token=token,
            browser=record.browser,
            created_at=now_wall,
        )

    def connect_native_client(
        self,
        *,
        client: Mapping[str, Any] | None = None,
    ) -> PairedClient:
        """Create a Chrome bridge session for the authenticated native host.

        The caller is trusted only after the desktop sidecar token has been
        validated by the HTTP adapter.  Chrome independently restricts native
        host launch to OpenWorker's stable extension origin, so no human pairing
        secret is needed on this path.  The returned bearer remains scoped to
        the constrained extension transport and is never the app token.
        """

        now_mono = self._clock()
        now_wall = self._wall_clock()
        with self._condition:
            self._cleanup_locked(now_mono)
            client_data = self._validate_client(
                client or {}, expected_browser="chrome"
            )
            token = secrets.token_urlsafe(32)
            token_hash = self._digest(token)
            session_id = secrets.token_urlsafe(18)
            session = _SessionRecord(
                session_id=session_id,
                token_hash=token_hash,
                browser="chrome",
                client=client_data,
                created_wall=now_wall,
                last_seen_mono=now_mono,
            )
            self._sessions[session_id] = session
            self._tokens[token_hash] = session_id
            self._condition.notify_all()
        return PairedClient(
            session_id=session_id,
            session_token=token,
            browser="chrome",
            created_at=now_wall,
        )

    @staticmethod
    def _validate_client(
        client: Mapping[str, Any], *, expected_browser: str
    ) -> dict[str, Any]:
        allowed = {"browser", "browser_version", "extension_version", "platform", "client_id"}
        unknown = set(client).difference(allowed)
        if unknown:
            raise PairingError(
                f"unsupported client fields: {', '.join(sorted(unknown))}",
                code="INVALID_CLIENT_METADATA",
            )
        normalized: dict[str, Any] = {}
        for key, value in client.items():
            if not isinstance(value, str) or len(value) > 256:
                raise PairingError(
                    f"client {key} must be a short string",
                    code="INVALID_CLIENT_METADATA",
                )
            normalized[key] = value
        claimed_browser = str(normalized.get("browser", expected_browser)).lower()
        if claimed_browser != expected_browser:
            raise PairingError(
                "client browser does not match the requested browser",
                code="BROWSER_MISMATCH",
            )
        normalized["browser"] = expected_browser
        return normalized

    def _authenticate_locked(self, token: str, now: float) -> _SessionRecord:
        if not isinstance(token, str) or not token:
            raise AuthenticationError("missing extension token", code="UNAUTHENTICATED")
        session_id = self._tokens.get(self._digest(token))
        session = self._sessions.get(session_id or "")
        if session is None or not session.connected:
            raise AuthenticationError("extension session is not active", code="UNAUTHENTICATED")
        if now - session.last_seen_mono >= self._session_idle:
            self._disconnect_locked(session, "idle_timeout", now)
            raise AuthenticationError("extension session expired", code="SESSION_EXPIRED")
        session.last_seen_mono = now
        return session

    def enqueue_command(
        self,
        session_id: str,
        command: str,
        params: Mapping[str, Any] | None = None,
    ) -> CommandTicket:
        try:
            normalized = validate_command(command, params or {})
        except ProtocolValidationError:
            raise
        now_mono = self._clock()
        now_wall = self._wall_clock()
        with self._condition:
            self._cleanup_locked(now_mono)
            session = self._sessions.get(session_id)
            if session is None or not session.connected:
                raise SessionNotFound(
                    "external browser session is not connected",
                    code="SESSION_NOT_FOUND",
                )
            tab_id = targeted_tab_id(command, normalized)
            if tab_id is not None and tab_id not in session.claimed_tab_ids:
                raise TabNotClaimed(
                    "The user has not shared this tab with OpenWorker",
                    code="TAB_NOT_CLAIMED",
                )
            unfinished = sum(
                item.result is None for item in session.commands.values()
            )
            if unfinished >= self._max_pending:
                raise ExternalBrowserBridgeError(
                    "external browser command queue is full", code="QUEUE_FULL"
                )
            request_id = secrets.token_urlsafe(18)
            record = _CommandRecord(
                request_id=request_id,
                command=command,
                params=normalized,
                created_wall=now_wall,
                created_mono=now_mono,
            )
            session.commands[request_id] = record
            session.order.append(request_id)
            self._condition.notify_all()
        return CommandTicket(
            session_id=session_id,
            request_id=request_id,
            command=command,
            created_at=now_wall,
        )

    def poll_commands(
        self,
        session_token: str,
        *,
        wait_seconds: float | None = None,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        wait = self._poll_timeout if wait_seconds is None else float(wait_seconds)
        wait = min(max(wait, 0), 30.0)
        limit = min(max(int(limit), 1), 8)
        deadline = self._clock() + wait
        with self._condition:
            while True:
                now = self._clock()
                self._cleanup_locked(now)
                session = self._authenticate_locked(session_token, now)
                ready: list[dict[str, Any]] = []
                for request_id in tuple(session.order):
                    command = session.commands.get(request_id)
                    if command is None or command.result is not None:
                        continue
                    if command.state != "queued":
                        continue
                    command.state = "leased"
                    command.leased_mono = now
                    command.attempts += 1
                    ready.append(command.to_wire())
                    if len(ready) >= limit:
                        break
                if ready:
                    return ready
                remaining = deadline - now
                if remaining <= 0:
                    return []
                self._condition.wait(timeout=remaining)

    def submit_result(
        self,
        session_token: str,
        request_id: str,
        *,
        ok: bool,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> BridgeResult:
        if not isinstance(ok, bool):
            raise ProtocolValidationError("ok must be a boolean")
        if ok and error is not None:
            raise ProtocolValidationError("a successful result cannot include error")
        if not ok and result is not None:
            raise ProtocolValidationError("a failed result cannot include result")
        normalized_result = validate_result_payload(result or {}) if ok else None
        normalized_error = (
            validate_error_payload(
                error
                or {
                    "code": "EXTENSION_COMMAND_FAILED",
                    "message": "The browser extension command failed",
                }
            )
            if not ok
            else None
        )
        now_mono = self._clock()
        now_wall = self._wall_clock()
        with self._condition:
            session = self._authenticate_locked(session_token, now_mono)
            command = session.commands.get(request_id)
            if command is None:
                raise CommandNotFound("browser request was not found", code="REQUEST_NOT_FOUND")
            if command.result is not None:
                # Idempotent retry from an extension whose response acknowledgement
                # was lost.  Conflicting retries are rejected.
                previous = command.result
                if (
                    previous.ok != ok
                    or previous.payload != normalized_result
                    or previous.error != normalized_error
                ):
                    raise ExternalBrowserBridgeError(
                        "browser request already has a different result",
                        code="RESULT_CONFLICT",
                    )
                return previous
            value = BridgeResult(
                session_id=session.session_id,
                request_id=request_id,
                ok=ok,
                completed_at=now_wall,
                payload=normalized_result,
                error=normalized_error,
            )
            command.state = "completed"
            command.result = value
            command.params.clear()
            self._condition.notify_all()
            return value

    def get_result(self, session_id: str, request_id: str) -> BridgeResult | None:
        with self._condition:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFound("external browser session was not found", code="SESSION_NOT_FOUND")
            command = session.commands.get(request_id)
            if command is None:
                raise CommandNotFound("browser request was not found", code="REQUEST_NOT_FOUND")
            return command.result

    def wait_for_result(
        self,
        session_id: str,
        request_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> BridgeResult | None:
        deadline = self._clock() + max(float(timeout_seconds), 0)
        with self._condition:
            while True:
                result = self.get_result(session_id, request_id)
                if result is not None:
                    return result
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)

    def cancel_command(
        self,
        session_id: str,
        request_id: str,
        *,
        reason: str = "caller_timeout",
    ) -> BridgeResult:
        """Expire a caller-abandoned ticket so it cannot execute later.

        A queued command is known not to have run. Once a mutating command has
        been leased, however, its outcome is inherently unknown; it is never
        put back on the queue. The returned terminal result lets a timeout race
        observe a just-submitted extension result without misreporting it.
        """

        with self._condition:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFound(
                    "external browser session was not found",
                    code="SESSION_NOT_FOUND",
                )
            command = session.commands.get(request_id)
            if command is None:
                raise CommandNotFound(
                    "browser request was not found", code="REQUEST_NOT_FOUND"
                )
            if command.result is not None:
                return command.result
            leased = command.state == "leased"
            outcome_unknown = (
                leased and command.command in self._MUTATING_COMMANDS
            )
            code = (
                "BROWSER_ACTION_OUTCOME_UNKNOWN"
                if outcome_unknown
                else "BROWSER_COMMAND_CANCELLED"
            )
            message = (
                "The browser action may have completed, but its outcome could not be confirmed"
                if outcome_unknown
                else "The browser command was cancelled before completion"
            )
            command.state = "completed"
            command.result = BridgeResult(
                session_id=session.session_id,
                request_id=command.request_id,
                ok=False,
                completed_at=self._wall_clock(),
                error={"code": code, "message": message},
            )
            command.params.clear()
            self._condition.notify_all()
            return command.result

    def publish_event(
        self, session_token: str, event: Mapping[str, Any]
    ) -> dict[str, Any]:
        normalized = validate_event(event)
        now = self._clock()
        with self._condition:
            session = self._authenticate_locked(session_token, now)
            event_type = normalized["type"]
            tab_id = normalized.get("tab_id")
            if event_type == "tab_claimed":
                session.claimed_tab_ids.add(tab_id)
            elif event_type in {"tab_released", "debugger_detached"}:
                session.claimed_tab_ids.discard(tab_id)
                self._fail_tab_commands_locked(session, tab_id, event_type)
            envelope = {
                **normalized,
                "received_at": self._wall_clock(),
                "session_id": session.session_id,
            }
            session.events.append(envelope)
            while len(session.events) > 256:
                session.events.popleft()
            self._condition.notify_all()
            return envelope

    def drain_events(self, session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._condition:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFound("external browser session was not found", code="SESSION_NOT_FOUND")
            values = []
            for _ in range(min(max(int(limit), 1), 256)):
                if not session.events:
                    break
                values.append(session.events.popleft())
            return values

    def session_state(self, session_id: str) -> dict[str, Any]:
        with self._condition:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFound("external browser session was not found", code="SESSION_NOT_FOUND")
            return {
                "session_id": session.session_id,
                "browser": session.browser,
                "client": dict(session.client),
                "connected": session.connected,
                "disconnect_reason": session.disconnect_reason,
                "claimed_tab_ids": sorted(session.claimed_tab_ids),
                "created_at": session.created_wall,
                "pending_commands": sum(
                    command.result is None for command in session.commands.values()
                ),
            }

    def disconnect(self, session_token: str, *, reason: str = "extension_disconnect") -> None:
        now = self._clock()
        with self._condition:
            session = self._authenticate_locked(session_token, now)
            self._disconnect_locked(session, reason[:128], now)
            self._condition.notify_all()

    def revoke_session(self, session_id: str, *, reason: str = "user_revoked") -> None:
        with self._condition:
            session = self._sessions.get(session_id)
            if session is None:
                return
            self._disconnect_locked(session, reason[:128], self._clock())
            self._condition.notify_all()

    def cleanup_expired(self) -> None:
        with self._condition:
            self._cleanup_locked(self._clock())

    def _cleanup_locked(self, now: float) -> None:
        expired_codes = [
            digest
            for digest, pairing in self._pairings.items()
            if pairing.expires_mono <= now
        ]
        for digest in expired_codes:
            self._pairings.pop(digest, None)
        for session in self._sessions.values():
            if session.connected and now - session.last_seen_mono >= self._session_idle:
                self._disconnect_locked(session, "idle_timeout", now)
                continue
            for command in session.commands.values():
                if (
                    command.result is None
                    and command.state == "leased"
                    and command.leased_mono is not None
                    and now - command.leased_mono >= self._lease_seconds
                ):
                    if command.command in self._MUTATING_COMMANDS:
                        command.state = "completed"
                        command.result = BridgeResult(
                            session_id=session.session_id,
                            request_id=command.request_id,
                            ok=False,
                            completed_at=self._wall_clock(),
                            error={
                                "code": "BROWSER_ACTION_OUTCOME_UNKNOWN",
                                "message": (
                                    "The browser action may have completed, but "
                                    "its outcome could not be confirmed"
                                ),
                            },
                        )
                        command.params.clear()
                    elif command.attempts >= self._max_attempts:
                        command.state = "completed"
                        command.result = BridgeResult(
                            session_id=session.session_id,
                            request_id=command.request_id,
                            ok=False,
                            completed_at=self._wall_clock(),
                            error={
                                "code": "EXTENSION_UNRESPONSIVE",
                                "message": "The browser extension did not acknowledge the command",
                            },
                        )
                        command.params.clear()
                    else:
                        command.state = "queued"
                        command.leased_mono = None
        self._condition.notify_all()

    def _disconnect_locked(
        self, session: _SessionRecord, reason: str, now: float
    ) -> None:
        if not session.connected:
            return
        session.connected = False
        session.disconnect_reason = reason
        session.claimed_tab_ids.clear()
        self._tokens.pop(session.token_hash, None)
        for command in session.commands.values():
            if command.result is None:
                command.state = "completed"
                command.result = BridgeResult(
                    session_id=session.session_id,
                    request_id=command.request_id,
                    ok=False,
                    completed_at=self._wall_clock(),
                    error={
                        "code": "EXTENSION_DISCONNECTED",
                        "message": "The browser extension disconnected before completing the command",
                    },
                )
                command.params.clear()

    def _fail_tab_commands_locked(
        self, session: _SessionRecord, tab_id: int, reason: str
    ) -> None:
        for command in session.commands.values():
            if command.result is not None:
                continue
            if targeted_tab_id(command.command, command.params) != tab_id:
                continue
            command.state = "completed"
            command.result = BridgeResult(
                session_id=session.session_id,
                request_id=command.request_id,
                ok=False,
                completed_at=self._wall_clock(),
                error={
                    "code": "TAB_RELEASED",
                    "message": f"The shared tab became unavailable ({reason})",
                },
            )
            command.params.clear()
