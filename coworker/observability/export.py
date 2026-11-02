"""Sending recordings to Langfuse or an OpenTelemetry endpoint (OB-6, phase-2 design §10).

Runs on the machine that ran the work, beside the recording store. Off by default: with no
destination the exporter is not created, so there is no thread, no DNS lookup and no socket.

- `ExportSettings`: the page's settings, in `observability.json` in the machine's state folder
  (never in a project's files, never in `config.toml`). Keys and header values are not here:
  they live in the app's `SecretStore`, profile `observability-export`.
- `managed_export`: the org policy's `observability_export` field. When a policy sets it, the
  policy's destination and switches are used and the page is read-only (TD2-11).
- `Exporter`: one thread that reads finished turns from the store, builds their spans
  (`export_spans.py`), sends them in batches and marks each step sent for the destination.
  The queue is the store: a restart resumes from the marks, and nothing is sent twice.
- `ObservabilityExport`: what the server holds. It answers the settings, status and test
  endpoints and creates or stops the exporter when the settings or the policy change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from . import export_spans as spans_mod
from .export_spans import Masker, build_turn_spans, encode_otlp, resource_attrs, switches_from

log = logging.getLogger(__name__)

SETTINGS_FILE = "observability.json"
PROFILE = "observability-export"
LANGFUSE_PATH = "/api/public/otel/v1/traces"
KINDS = ("off", "langfuse", "otlp")
RETENTION_CHOICES = (7, 30, 90)
MAX_HEADERS = 8
INCLUDE_KEYS = ("messages", "tool_io", "context", "names")

BATCH_SPANS = 200
FLUSH_SECONDS = 5.0
BACKOFF_START = 1.0
BACKOFF_CAP = 300.0
TIMEOUT_SECONDS = 20.0

REFUSED_KEY = "The destination refused the key"
NOT_FOUND = "Address not found"


# -- what is chosen ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Destination:
    kind: str = "off"
    host: str = ""  # Langfuse
    url: str = ""  # OpenTelemetry endpoint
    header_names: tuple[str, ...] = ()  # OpenTelemetry endpoint; values are secrets

    @property
    def endpoint(self) -> str:
        if self.kind == "langfuse":
            return self.host.rstrip("/") + LANGFUSE_PATH
        return self.url if self.kind == "otlp" else ""

    @property
    def dialect(self) -> str:
        return "langfuse" if self.kind == "langfuse" else "semconv"

    @property
    def key(self) -> str:
        """Identifies the destination for the sent marks: kind and address."""
        return f"{self.kind}:{self.endpoint}" if self.kind != "off" else ""

    @property
    def place(self) -> str:
        """The address's host, for the page and the session notice."""
        return urlparse(self.endpoint).netloc or self.endpoint

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.kind == "langfuse":
            out["host"] = self.host
        elif self.kind == "otlp":
            out["url"] = self.url
            out["header_names"] = list(self.header_names)
        return out


def _include(raw: Any) -> dict[str, bool]:
    raw = raw if isinstance(raw, dict) else {}
    return {k: bool(raw.get(k)) for k in INCLUDE_KEYS}


def _http_url(value: Any) -> str:
    v = str(value or "").strip()
    return v if urlparse(v).scheme in ("http", "https") and urlparse(v).netloc else ""


def parse_destination(raw: Any) -> Optional[Destination]:
    """A destination from settings or a policy; None when it does not make sense."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "off").lower()
    if kind == "off":
        return Destination()
    if kind == "langfuse":
        host = _http_url(raw.get("host"))
        return Destination(kind="langfuse", host=host.rstrip("/")) if host else None
    if kind == "otlp":
        url = _http_url(raw.get("url"))
        names = raw.get("header_names")
        if names is None and isinstance(raw.get("headers"), dict):
            names = list(raw["headers"])
        names = [str(n).strip() for n in (names or []) if str(n).strip()]
        if not url or len(names) > MAX_HEADERS:
            return None
        return Destination(kind="otlp", url=url, header_names=tuple(dict.fromkeys(names)))
    return None


@dataclass
class ExportSettings:
    record: bool = True
    retention_days: int = 30
    destination: Destination = field(default_factory=Destination)
    include: dict[str, bool] = field(default_factory=lambda: _include({}))
    # When each destination was chosen: only turns that end after it are sent (TD2-12).
    since: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "ExportSettings":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        days = raw.get("retention_days")
        return cls(
            record=raw.get("record", True) is not False,
            retention_days=days if days in RETENTION_CHOICES else 30,
            destination=parse_destination(raw.get("destination")) or Destination(),
            include=_include(raw.get("include")),
            since={str(k): int(v) for k, v in (raw.get("since") or {}).items() if isinstance(v, (int, float))},
        )

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"record": self.record, "retention_days": self.retention_days,
                "destination": self.destination.public(), "include": self.include, "since": self.since}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)


# -- company control (TD2-11) ------------------------------------------------------------------
@dataclass
class Managed:
    by: str
    name: str
    at: str
    destination: Destination
    include: dict[str, bool]


POLICY_FIELD = "observability_export"
_POLICY_SECRET_KEYS = ("public_key", "secret_key", "headers")


def managed_export(policy: Any) -> Optional[Managed]:
    """The org policy's `observability_export` field, read like `audit_export`: anything
    malformed reads as not set, so governance never turns on by accident.

    Shape: `{"enabled": true, "name": "...", "set_at": "...", "destination": {"kind":
    "langfuse", "host": ...} | {"kind": "otlp", "url": ..., "header_names": [...]} |
    {"kind": "off"}, "include": {"messages", "tool_io", "context", "names"}}`. The keys
    and header values travel in the same field and are moved to the secret store
    (`take_policy_secrets`) before the policy is saved."""
    raw = policy.get(POLICY_FIELD) if isinstance(policy, dict) else None
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return None
    dest = parse_destination(raw.get("destination"))
    if dest is None:
        return None
    by = str(policy.get("org_name") or policy.get("org_id") or "your organisation")
    return Managed(by=by, name=str(raw.get("name") or ""), at=str(raw.get("set_at") or ""),
                   destination=dest, include=_include(raw.get("include")))


def take_policy_secrets(policy: dict[str, Any], secrets: Any) -> dict[str, Any]:
    """Move the policy's export keys into the secret store (owned by the policy) and return
    the policy without them, so `policy.json` never holds them. A policy without the field
    removes the keys it had stored."""
    if not isinstance(policy, dict):
        return policy
    raw = policy.get(POLICY_FIELD)
    held = dict(secrets.get(PROFILE) or {}) if secrets is not None else {}
    if not isinstance(raw, dict):
        if secrets is not None and "policy" in held:
            held.pop("policy")
            _put_profile(secrets, held)
        return policy
    dest = raw.get("destination") if isinstance(raw.get("destination"), dict) else {}
    found = {k: dest[k] for k in _POLICY_SECRET_KEYS if k in dest}
    if secrets is not None and found:
        if isinstance(found.get("headers"), dict):
            found["headers"] = {str(k): str(v) for k, v in found["headers"].items()}
            dest = {**dest, "header_names": list(found["headers"])}
        held["policy"] = {k: v for k, v in found.items()}
        _put_profile(secrets, held)
    clean_dest = {k: v for k, v in dest.items() if k not in _POLICY_SECRET_KEYS}
    return {**policy, POLICY_FIELD: {**raw, "destination": clean_dest}}


def _put_profile(secrets: Any, data: dict[str, Any]) -> None:
    if data:
        secrets.put(PROFILE, data)
    else:
        secrets.delete(PROFILE)


def _mask_hint(value: str, *, keep_end: bool) -> str:
    if not value:
        return ""
    prefix = value[:6] if value[:3] in ("pk-", "sk-") else ""
    tail = value[-4:] if keep_end and len(value) >= 16 else ""
    return f"{prefix}{'•' * 8}{tail}"


# -- sending -----------------------------------------------------------------------------------
Post = Callable[[str, bytes, dict[str, str], float], tuple[int, str]]


def http_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    """POST one OTLP/HTTP JSON body. A network error is status 0 with its kind as the text
    (never the URL or anything from the request)."""
    import httpx

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, content=body, headers=headers)
        return resp.status_code, resp.text[:500]
    except httpx.TimeoutException:
        return 0, "timed out"
    except httpx.HTTPError as exc:
        return 0, type(exc).__name__


class _Retry(Exception):
    pass


class _Refused(Exception):
    pass


@dataclass
class ExportConfig:
    destination: Destination
    headers: dict[str, str]  # auth and the endpoint's own headers: secrets
    include: dict[str, bool]
    since_ms: int
    managed: bool = False


def _reason(status: int, text: str) -> str:
    if status == 0:
        return f"no answer ({text})" if text else "no answer"
    return f"HTTP {status}" + (f": {text.strip()[:200]}" if text.strip() else "")


class Exporter:
    """Reads finished turns from the store and sends them, each step once."""

    def __init__(self, store: Any, config: ExportConfig, *, post: Post = http_post,
                 secret_values: Callable[[], list[str]] = list,
                 session_title: Callable[[str], Optional[str]] = lambda _sid: None,
                 batch_spans: int = BATCH_SPANS, flush_seconds: float = FLUSH_SECONDS,
                 backoff_start: float = BACKOFF_START, backoff_cap: float = BACKOFF_CAP) -> None:
        self.store = store
        self.config = config
        self.post = post
        self.secret_values = secret_values
        self.session_title = session_title
        self.batch_spans = batch_spans
        self.flush_seconds = flush_seconds
        self.backoff_start, self.backoff_cap = backoff_start, backoff_cap
        self.state = "sending"
        self.last_error = ""
        self.last_sent_at: Optional[float] = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        store.export_destination = (config.destination.key, config.since_ms)

    # -- the thread ------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="observability-export", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        if getattr(self.store, "export_destination", None) == (self.config.destination.key, self.config.since_ms):
            self.store.export_destination = None

    def set_include(self, include: dict[str, bool]) -> None:
        """Switches changed: applied per turn, when the turn is sent (§10.9)."""
        self.config = replace(self.config, include=dict(include))

    def _run(self) -> None:
        backoff = self.backoff_start
        while not self._stop.is_set():
            try:
                self.flush_once()
                backoff = self.backoff_start
                self.state, self.last_error = "sending", ""
                self._wake.wait(self.flush_seconds)
                self._wake.clear()
            except _Retry as exc:
                self.state, self.last_error = "retrying", str(exc)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.backoff_cap)
            except _Refused as exc:
                # Stops until the settings change (a new exporter is made then).
                self.state, self.last_error = "failed", str(exc)
                self.store.count_export(self.config.destination.key, failed=1)
                self._stop.wait()
            except Exception as exc:  # noqa: BLE001 - never let the thread die quietly
                log.exception("observability export: unexpected failure")
                self.state, self.last_error = "retrying", type(exc).__name__
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.backoff_cap)

    # -- one pass --------------------------------------------------------------------------------
    def flush_once(self) -> int:
        """Send everything ready, in batches. Returns the spans sent. Raises `_Retry` (keep
        the batch, back off) or `_Refused` (stop until the settings change)."""
        cfg = self.config
        dest = cfg.destination.key
        sw = switches_from(cfg.include)
        masker = Masker(self.secret_values()) if any(sw.values()) else Masker()
        sent = 0
        done: set[str] = set()  # turns with nothing left to send in this pass
        while not self._stop.is_set():
            queue = [t for t in self.store.export_queue(dest, cfg.since_ms, limit=50) if t not in done]
            if not queue:
                return sent
            pending: list[tuple[dict[str, Any], list[tuple[str, str]]]] = []
            for tid in queue:
                built = self._turn_spans(tid, sw, masker)
                if not built:
                    done.add(tid)
                pending.extend(built)
            for i in range(0, len(pending), self.batch_spans):
                sent += self._send(pending[i:i + self.batch_spans])
            if not pending:
                return sent

    def _turn_spans(self, turn_id: str, sw: dict[str, bool], masker: Masker) -> list[tuple[dict[str, Any], list[tuple[str, str]]]]:
        cfg = self.config
        dest = cfg.destination.key
        loaded = self.store.load_turn(turn_id)
        if loaded is None:
            return []
        turn, steps = loaded
        marked = self.store.export_marked(dest, turn_id)
        ready = {s["step_id"] for s in steps
                 if s.get("status") not in (None, "running") and s["step_id"] not in marked}
        include_root = turn_id not in marked
        late_notices = [s["step_id"] for s in steps if s.get("kind") == "notice" and s["step_id"] in ready]
        if not include_root and late_notices:
            # A notice is an event on the turn's span, which has gone already.
            self.store.mark_export(dest, [(n, turn_id) for n in late_notices], "skipped")
        title = self.session_title(turn["session_id"]) if sw.get("names") else None
        built = build_turn_spans(turn, steps, sw=sw, dialect=cfg.destination.dialect,
                                 get_piece=self.store.get_piece, masker=masker,
                                 include_root=include_root, only=ready, session_title=title)
        stamp = turn.get("stamp") or {}
        machine = stamp.get("machine") or {}
        resource = resource_attrs(app_version=str(machine.get("app_version") or ""),
                                  machine_name=str(machine.get("name") or "") if sw.get("names") else None)
        out = []
        for span in built:
            marks = [(span["_mark"], turn_id)] + [(n, turn_id) for n in span.get("_notices") or []]
            out.append(({**span, "_resource": resource}, marks))
        return out

    def _encode(self, items: list[tuple[dict[str, Any], list[tuple[str, str]]]]) -> bytes:
        groups: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        for span, _ in items:
            res = span["_resource"]
            groups.setdefault(json.dumps(res, sort_keys=True), (res, []))[1].append(span)
        return json.dumps(encode_otlp(list(groups.values())), ensure_ascii=False).encode("utf-8")

    def _post(self, items: list[tuple[dict[str, Any], list[tuple[str, str]]]]) -> tuple[int, str]:
        headers = {"Content-Type": "application/json", **self.config.headers}
        return self.post(self.config.destination.endpoint, self._encode(items), headers, TIMEOUT_SECONDS)

    def _send(self, batch: list[tuple[dict[str, Any], list[tuple[str, str]]]]) -> int:
        status, text = self._post(batch)
        if 200 <= status < 300:
            self._mark(batch, "sent")
            return len(batch)
        self._raise_for(status, text)
        # 400 and the other 4xx: split the batch once; a span refused on its own is dropped.
        sent = 0
        for item in batch:
            status, text = self._post([item])
            if 200 <= status < 300:
                self._mark([item], "sent")
                sent += 1
                continue
            self._raise_for(status, text)
            self._mark([item], "dropped")
            self.last_error = _reason(status, text)
            log.info("observability export: a span was refused on its own (%s)", status)
        return sent

    def _raise_for(self, status: int, text: str) -> None:
        if status in (401, 403):
            raise _Refused(REFUSED_KEY)
        if status == 404:
            raise _Refused(NOT_FOUND)
        if status == 0 or status == 429 or status >= 500:
            raise _Retry(_reason(status, text))

    def _mark(self, batch: list[tuple[dict[str, Any], list[tuple[str, str]]]], state: str) -> None:
        marks = [m for _, ms in batch for m in ms]
        self.store.mark_export(self.config.destination.key, marks, state)
        if state == "sent":
            self.last_sent_at = time.time()


# -- what the server holds ---------------------------------------------------------------------
class ObservabilityExport:
    """Settings, company control, status and the exporter's life (design §10.5 to §10.8)."""

    def __init__(self, state: Path, secrets: Any, store: Any, *,
                 session_title: Callable[[str], Optional[str]] = lambda _sid: None,
                 post: Post = http_post, exporter_options: Optional[dict[str, Any]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(state) / SETTINGS_FILE
        self.secrets = secrets
        self.store = store
        self.session_title = session_title
        self.post = post
        self.exporter_options = dict(exporter_options or {})
        self.clock = clock
        self.settings = ExportSettings.load(self.path)
        self.managed: Optional[Managed] = None
        self.exporter: Optional[Exporter] = None
        self._lock = threading.RLock()
        if store is not None:
            store.retention_days = self.settings.retention_days

    # -- what is in force --------------------------------------------------------------------------
    def effective(self) -> tuple[Destination, dict[str, bool]]:
        if self.managed is not None:
            return self.managed.destination, self.managed.include
        return self.settings.destination, self.settings.include

    def _held(self) -> dict[str, Any]:
        data = (self.secrets.get(PROFILE) if self.secrets is not None else None) or {}
        return data if isinstance(data, dict) else {}

    def _keys(self) -> dict[str, Any]:
        """The keys for the destination in force: the policy's when it manages the page."""
        held = self._held()
        own = held.get("policy" if self.managed is not None else "user") or {}
        return own if isinstance(own, dict) else {}

    def _headers(self, dest: Destination) -> dict[str, str]:
        keys = self._keys()
        if dest.kind == "langfuse":
            import base64

            pair = f"{keys.get('public_key') or ''}:{keys.get('secret_key') or ''}"
            return {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()}
        if dest.kind == "otlp":
            values = keys.get("headers") if isinstance(keys.get("headers"), dict) else {}
            return {n: str(values[n]) for n in dest.header_names if values.get(n)}
        return {}

    def secret_values(self) -> list[str]:
        """Masking (R4): every value in the secret store, and credential-looking env vars."""
        data = self.secrets._read() if self.secrets is not None and hasattr(self.secrets, "_read") else {}
        return spans_mod.store_secret_values(data) + spans_mod.env_secret_values()

    def content_notice(self) -> Optional[str]:
        """When the company's policy sends content: what goes where, for each new session."""
        if self.managed is None or self.managed.destination.kind == "off":
            return None
        inc = self.managed.include
        what = [w for k, w in (("messages", "messages"), ("tool_io", "tool inputs and results"),
                               ("context", "the context OpenWorker adds"), ("names", "names")) if inc.get(k)]
        if not what:
            return None
        listed = what[0] if len(what) == 1 else ", ".join(what[:-1]) + " and " + what[-1]
        dest = self.managed.destination
        where = "Langfuse" if dest.kind == "langfuse" else "OpenTelemetry"
        return f"{self.managed.by} receives this session's {listed} ({where}, {dest.place})."

    # -- the exporter's life ------------------------------------------------------------------------
    def reconcile(self) -> None:
        """Create, keep or stop the exporter for what is in force. Off: none at all."""
        with self._lock:
            dest, include = self.effective()
            if dest.kind == "off" or not self.settings.record:
                self._stop_exporter()
                return
            since = self._since(dest)
            cfg = ExportConfig(destination=dest, headers=self._headers(dest), include=dict(include),
                               since_ms=since, managed=self.managed is not None)
            old = self.exporter
            if old is not None and old.state != "failed" and old.config.destination == dest and old.config.headers == cfg.headers \
                    and old.config.since_ms == since:
                old.set_include(include)
                old.wake()
                return
            self._stop_exporter()
            self.exporter = Exporter(self.store, cfg, post=self.post, secret_values=self.secret_values,
                                     session_title=self.session_title, **self.exporter_options)
            self.exporter.start()

    def _since(self, dest: Destination) -> int:
        """When this destination was chosen: older turns are not sent (TD2-12)."""
        since = self.settings.since.get(dest.key)
        if since is None:
            since = int(self.clock() * 1000)
            self.settings.since[dest.key] = since
            self.settings.save(self.path)
        return since

    def _stop_exporter(self) -> None:
        if self.exporter is not None:
            self.exporter.stop()
            self.exporter = None

    def wake(self) -> None:
        """A turn ended: send soon."""
        ex = self.exporter
        if ex is not None:
            ex.wake()

    def close(self) -> None:
        with self._lock:
            self._stop_exporter()

    # -- the policy -----------------------------------------------------------------------------------
    def apply_policy(self, policy: Optional[dict[str, Any]]) -> None:
        with self._lock:
            self.managed = managed_export(policy or {})
            self.reconcile()

    # -- GET/PUT settings (§10.8) ---------------------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        dest, include = self.effective()
        keys = self._keys()
        secrets: dict[str, Any] = {}
        if dest.kind == "langfuse":
            secrets = {"public_key": _mask_hint(str(keys.get("public_key") or ""), keep_end=True),
                       "secret_key": _mask_hint(str(keys.get("secret_key") or ""), keep_end=False)}
        elif dest.kind == "otlp":
            values = keys.get("headers") if isinstance(keys.get("headers"), dict) else {}
            secrets = {"headers": {n: ("•" * 8 if values.get(n) else "") for n in dest.header_names}}
        out = {
            "record": self.settings.record,
            "retention_days": self.settings.retention_days,
            "destination": dest.public(),
            "include": dict(include),
            "secrets": secrets,  # masked hints only: values never leave
            "managed": None,
            "notice": self.content_notice(),
        }
        if self.managed is not None:
            out["managed"] = {"by": self.managed.by, "name": self.managed.name, "at": self.managed.at}
        return out

    def put_settings(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Returns (HTTP status, answer). 409 when the page is managed by a policy."""
        if not isinstance(body, dict):
            return 400, {"ok": False, "error": "expected an object"}
        with self._lock:
            s = self.settings
            if "record" in body:
                s.record = bool(body["record"])
            if "retention_days" in body:
                if body["retention_days"] not in RETENTION_CHOICES:
                    return 400, {"ok": False, "error": "retention_days must be 7, 30 or 90"}
                s.retention_days = int(body["retention_days"])
                if self.store is not None:
                    self.store.retention_days = s.retention_days
            touches_export = any(k in body for k in ("destination", "include", "secrets"))
            if touches_export and self.managed is not None:
                self.settings.save(self.path)
                return 409, {"ok": False, "error": f"Managed by {self.managed.by}"}
            if "destination" in body:
                dest = parse_destination(body["destination"])
                if dest is None:
                    return 400, {"ok": False, "error": "check the address (http or https) and at most 8 headers"}
                if dest != s.destination:
                    s.destination = dest
                    if dest.kind != "off":  # a new choice starts from now (TD2-12)
                        s.since[dest.key] = int(self.clock() * 1000)
            if "include" in body:
                s.include = _include({**s.include, **(body["include"] if isinstance(body["include"], dict) else {})})
            if isinstance(body.get("secrets"), dict):
                self._put_user_secrets(body["secrets"], s.destination)
            s.save(self.path)
            self.reconcile()
            return 200, {"ok": True, **self.get_settings()}

    def _put_user_secrets(self, given: dict[str, Any], dest: Destination) -> None:
        held = self._held()
        user = dict(held.get("user") or {})
        for k in ("public_key", "secret_key"):
            v = given.get(k)
            if isinstance(v, str) and v.strip():
                user[k] = v.strip()
        if isinstance(given.get("headers"), dict):
            headers = dict(user.get("headers") or {})
            for n, v in given["headers"].items():
                if isinstance(v, str) and v:
                    headers[str(n)] = v
            user["headers"] = headers
        if dest.kind == "otlp" and isinstance(user.get("headers"), dict):
            # A header taken off the list takes its value with it.
            user["headers"] = {n: v for n, v in user["headers"].items() if n in dest.header_names}
        held["user"] = user
        _put_profile(self.secrets, {k: v for k, v in held.items() if v})

    # -- status (§10.5) ----------------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        dest, _ = self.effective()
        ex = self.exporter
        if dest.kind == "off" or ex is None:
            return {"state": "off", "last_sent_at": None, "sent": 0, "sent_today": 0, "failed": 0,
                    "dropped": 0, "last_error": "", "destination": dest.public()}
        midnight = time.mktime(time.localtime(self.clock())[:3] + (0, 0, 0, 0, 0, -1)) * 1000
        counts = self.store.export_counts(dest.key)
        today = self.store.export_counts(dest.key, since_ms=int(midnight))["sent"]
        state = ex.state
        if state == "sending" and self.managed is not None:
            state = "managed"
        return {"state": state, "last_sent_at": ex.last_sent_at, "sent": counts["sent"], "sent_today": today,
                "failed": counts["failed"], "dropped": counts["dropped"], "last_error": ex.last_error,
                "destination": dest.public()}

    # -- the test recording (E9c) ---------------------------------------------------------------------
    def send_test(self) -> dict[str, Any]:
        dest, _ = self.effective()
        if dest.kind == "off":
            return {"ok": False, "status": 0, "text": "Choose where to send recordings first."}
        spans = spans_mod.sample_spans(int(self.clock() * 1000), dest.dialect)
        from .stamps import machine_stamp

        res = resource_attrs(app_version=str(machine_stamp().get("app_version") or ""))
        body = json.dumps(encode_otlp([(res, spans)])).encode("utf-8")
        started = time.monotonic()
        status, text = self.post(dest.endpoint, body, {"Content-Type": "application/json", **self._headers(dest)},
                                 TIMEOUT_SECONDS)
        ms = int((time.monotonic() - started) * 1000)
        if 200 <= status < 300:
            answer = "Sent. Open Langfuse to see it." if dest.kind == "langfuse" else "Sent."
            return {"ok": True, "status": status, "text": answer, "ms": ms}
        if status in (401, 403):
            return {"ok": False, "status": status, "text": REFUSED_KEY, "ms": ms}
        return {"ok": False, "status": status, "text": _reason(status, text), "ms": ms}


def destination_digest(dest: Destination) -> str:
    """A short, stable label for a destination (logs never show its address)."""
    return hashlib.sha256(dest.key.encode()).hexdigest()[:8]
