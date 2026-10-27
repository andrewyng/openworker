"""llama.cpp on this Mac, set up and run by OpenWorker (UX-057).

The llama.cpp page offers three steps: get llama.cpp (Homebrew), pick a model sized to
this machine, and start it. After that OpenWorker runs `llama-server` itself, one model at
a time, and points the `llamacpp` provider at it. "Connect to it instead" (a server the
user runs) stays the plain provider form; nothing here touches it unless the user starts
a model.

Where models go: llama.cpp's own cache, the Hugging Face layout `llama-server -hf` uses
(`$LLAMA_CACHE`, else the Hugging Face hub cache, `~/.cache/huggingface/hub`). OpenWorker
downloads the files itself, into that layout, because `-hf` cannot fetch a model's second
file (the MTP draft) and gives no progress; a later `llama-server -hf` finds them there.
Models Ollama already downloaded are GGUF files too, and are loaded in place.

The desktop app ends its server with SIGKILL, so no shutdown code runs on quit. Two guards
keep a model from holding memory after OpenWorker is gone: a small watcher process kills
llama-server when this server exits, and the pid file lets the next start stop one left
over.

The GUI polls `state()` while something runs, like the sandbox setup (setup_job.py).
"""

from __future__ import annotations

import datetime
import functools
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from . import local_machine
from .recommended import recommendation_for

_CATALOG = Path(__file__).with_name("llamacpp_models.json")
HF = "https://huggingface.co"
DEFAULT_PORT = 8080
LOAD_TIMEOUT_S = 900.0
CHECK_TIMEOUT_S = 180.0
INSTALL_LINES = 6
# Hugging Face answers 429 with how long to wait (per-IP windows of five minutes). Wait
# that long, within reason, a few times; then give up with the error.
RATE_LIMIT_RETRIES = 6
RATE_LIMIT_WAIT_MAX_S = 120
RATE_LIMIT_WAIT_DEFAULT_S = 5

# Where Homebrew puts programs on Apple silicon and on Intel Macs; the desktop app's PATH
# often has neither.
_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def supported() -> bool:
    """The guided setup is Mac-only for now (ruled 2026-10-06)."""
    return sys.platform == "darwin"


def _which(program: str) -> Optional[str]:
    found = shutil.which(program)
    if found:
        return found
    for d in _BIN_DIRS:
        p = Path(d) / program
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def find_llama_server() -> Optional[str]:
    return _which("llama-server")


def find_brew() -> Optional[str]:
    return _which("brew")


def llama_version(path: Optional[str]) -> Optional[str]:
    """`llama-server --version` prints `version: 0.6.0 (build …)` on stderr."""
    if not path:
        return None
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"version:\s*([^\s(]+)", (out.stdout or "") + (out.stderr or ""))
    return m.group(1) if m else None


def cache_dir() -> Path:
    """The folder llama.cpp keeps downloads in, resolved the way llama.cpp 0.6 does."""
    env = os.environ
    if env.get("LLAMA_CACHE"):
        return Path(env["LLAMA_CACHE"]).expanduser()
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if env.get(key):
            return Path(env[key]).expanduser()
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]).expanduser() / "hub"
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]).expanduser() / "huggingface" / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _pretty(path: Path) -> str:
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home) else s


def _free_bytes(path: Path) -> Optional[int]:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


class _Cancelled(Exception):
    pass


# -- the models it offers --------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def catalog() -> tuple[dict[str, Any], ...]:
    try:
        raw = json.loads(_CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(raw.get("models") or ())


def _catalog_entry(model_id: str) -> Optional[dict[str, Any]]:
    return next((m for m in catalog() if m.get("id") == model_id), None)


def _repo_dir(repo: str) -> Path:
    return cache_dir() / ("models--" + repo.replace("/", "--"))


def _cached_file(repo: str, filename: str) -> Optional[Path]:
    """A file already in the cache (any snapshot), else None."""
    snaps = _repo_dir(repo) / "snapshots"
    if not snaps.is_dir():
        return None
    for snap in snaps.iterdir():
        p = snap / filename
        if p.exists():
            return p
    return None


def _downloaded(entry: dict[str, Any]) -> bool:
    files = [entry["file"], *([entry["draft"]] if entry.get("draft") else [])]
    return all(_cached_file(entry["repo"], f) for f in files)


def ollama_models_dir() -> Path:
    env = os.environ.get("OLLAMA_MODELS", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".ollama" / "models"


def ollama_models() -> list[dict[str, Any]]:
    """The models Ollama downloaded, each with its GGUF file, read from Ollama's manifests
    (no Ollama server needed). Names as Ollama shows them: `qwen3-coder:30b`."""
    root = ollama_models_dir()
    manifests = root / "manifests"
    if not manifests.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(manifests.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(manifests).parts  # host / namespace / model / tag
        if len(parts) != 4:
            continue
        host, ns, model, tag = parts
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        layer = next(
            (l for l in doc.get("layers") or [] if l.get("mediaType") == "application/vnd.ollama.image.model"),
            None,
        )
        if not layer or not str(layer.get("digest", "")).startswith("sha256:"):
            continue
        blob = root / "blobs" / layer["digest"].replace(":", "-")
        if not blob.is_file():
            continue
        if host == "registry.ollama.ai":
            name = f"{model}:{tag}" if ns == "library" else f"{ns}/{model}:{tag}"
        else:
            name = f"{host}/{ns}/{model}:{tag}"
        out.append({"name": name, "path": str(blob), "size": int(layer.get("size") or 0)})
    return out


def overview() -> dict[str, Any]:
    """Everything the setup card shows: whether llama.cpp is here, where models go, what
    to run, and what is running."""
    memory = local_machine.model_memory_bytes()
    server = find_llama_server()
    have_ollama = {m["name"] for m in ollama_models()}
    suggested = []
    ollama_pull = []
    for m in catalog():
        fit = local_machine.fit_for(m.get("size"), memory)
        if fit == "too_large":
            continue
        suggested.append(
            {
                "id": m["id"],
                "name": m["name"],
                "vendor": m.get("vendor"),
                "quant": m.get("quant"),
                "features": m.get("features") or [],
                "size": m.get("size"),
                "fit": fit,
                "downloaded": _downloaded(m),
                "license": m.get("license"),
                "license_url": m.get("license_url"),
            }
        )
        if m.get("ollama") and m["ollama"] not in have_ollama:
            ollama_pull.append({"name": m["ollama"], "size": m.get("size"), "fit": fit, "command": f"ollama pull {m['ollama']}"})
    on_mac = [
        {**m, "fit": local_machine.fit_for(m["size"], memory)}
        for m in ollama_models()
        if local_machine.fit_for(m["size"], memory) != "too_large"
    ]
    cache = cache_dir()
    return {
        "supported": supported(),
        "brew": find_brew(),
        "installed": {"path": server, "version": llama_version(server)} if server else None,
        "install_command": "brew install llama.cpp",
        "cache_dir": _pretty(cache),
        "cache_free_bytes": _free_bytes(cache),
        "suggested": suggested,
        "ollama": on_mac,
        "ollama_pull": ollama_pull,
        **runner().state(),
    }


# -- downloads into llama.cpp's cache --------------------------------------------------------


def _retry_after(resp: httpx.Response) -> int:
    """Seconds to wait after a 429, from `Retry-After` (seconds or a date), `RateLimit-Reset`,
    or Hugging Face's `RateLimit: "resolvers";r=0;t=52` — bounded, at least one second."""
    h = resp.headers
    wait: Optional[float] = None
    raw = h.get("retry-after")
    if raw:
        try:
            wait = float(raw)
        except ValueError:
            try:
                wait = (parsedate_to_datetime(raw) - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                wait = None
    if wait is None and h.get("ratelimit-reset"):
        try:
            wait = float(h["ratelimit-reset"])
        except ValueError:
            wait = None
    if wait is None and h.get("ratelimit"):
        m = re.search(r"\bt=(\d+)", h["ratelimit"])
        wait = float(m.group(1)) if m else None
    if wait is None:
        wait = RATE_LIMIT_WAIT_DEFAULT_S
    return int(min(max(wait, 1), RATE_LIMIT_WAIT_MAX_S))


def _hf_request(method: str, url: str, *, on_wait: Optional[Callable[[int], None]] = None, **kw: Any) -> httpx.Response:
    """One Hugging Face request; on 429 it waits as told and tries again, a few times."""
    kw.setdefault("timeout", 30)
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        resp = httpx.request(method, url, **kw)
        if resp.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
            return resp
        wait = _retry_after(resp)
        if on_wait:
            on_wait(wait)
        else:
            time.sleep(wait)
    return resp  # pragma: no cover - the loop returns


def _hf_files(repo: str, on_wait: Optional[Callable[[int], None]] = None) -> tuple[str, dict[str, dict[str, Any]]]:
    """(commit, {path: {size, oid, sha256}}) for a repo's main branch. `sha256` is the LFS
    object id, the checksum the download is verified against; None for a non-LFS file."""
    rev = _hf_request("GET", f"{HF}/api/models/{repo}/revision/main", on_wait=on_wait, follow_redirects=True)
    rev.raise_for_status()
    commit = rev.json()["sha"]
    tree = _hf_request("GET", f"{HF}/api/models/{repo}/tree/{commit}", on_wait=on_wait, follow_redirects=True)
    tree.raise_for_status()
    files = {}
    for f in tree.json():
        lfs = f.get("lfs") or {}
        oid = lfs.get("oid") or f.get("oid")
        files[f["path"]] = {"size": int(lfs.get("size") or f.get("size") or 0), "oid": oid, "sha256": lfs.get("oid")}
    return commit, files


def _hf_resolve_info(repo: str, filename: str, on_wait: Optional[Callable[[int], None]] = None) -> tuple[str, dict[str, Any]]:
    """What the file's own download address says about it, for when the API is not
    answering: the 302 to the CDN carries the commit, the size and the ETag. The ETag names
    the blob as huggingface_hub does; it is not trusted as a checksum."""
    resp = _hf_request("HEAD", f"{HF}/{repo}/resolve/main/{filename}", on_wait=on_wait, follow_redirects=False)
    if resp.status_code not in (200, 302, 307):
        raise RuntimeError(f"download failed: HTTP {resp.status_code}")
    h = resp.headers
    etag = (h.get("x-linked-etag") or h.get("etag") or "").strip().removeprefix("W/").strip('"')
    size = int(h.get("x-linked-size") or h.get("content-length") or 0)
    if not etag or not size:
        raise RuntimeError(f"download failed: no size for {filename}")
    return h.get("x-repo-commit") or "main", {"size": size, "oid": etag, "sha256": None}


def _sha256(path: Path, cancel: Optional[threading.Event] = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            chunk = fh.read(8 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _place(repo: str, commit: str, filename: str, blob: Path) -> Path:
    """Link a downloaded blob into the snapshot folder, as huggingface_hub does."""
    snap = _repo_dir(repo) / "snapshots" / commit
    snap.mkdir(parents=True, exist_ok=True)
    target = snap / filename
    if not target.exists():
        try:
            target.symlink_to(os.path.relpath(blob, snap))
        except OSError:
            shutil.copyfile(blob, target)
    refs = _repo_dir(repo) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(commit, encoding="utf-8")
    return target


# -- the managed server ---------------------------------------------------------------------


def _state_path(name: str) -> Path:
    from ..secrets import state_dir

    return state_dir() / name


def _load_settings() -> dict[str, Any]:
    try:
        data = json.loads(_state_path("llamacpp.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict[str, Any]) -> None:
    p = _state_path("llamacpp.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # As llama-server binds: a port a stopped server just left (TIME_WAIT) is free.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port() -> int:
    for port in range(DEFAULT_PORT, DEFAULT_PORT + 20):
        if _port_free(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_llama_server(pid: int) -> bool:
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.stdout.strip().endswith("llama-server")


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _end(proc: subprocess.Popen) -> None:
    """Stop a child of ours: SIGTERM, then SIGKILL after 5 s. Waiting on it also reaps it
    (a zombie still answers `kill -0`, so `_kill` would wait the full time)."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)


class LlamaCppRunner:
    """Installs llama.cpp and runs one llama-server. `phase` is one of idle, installing,
    downloading, loading, checking, running, failed. One thing at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._watcher: Optional[subprocess.Popen] = None
        self.phase = "idle"
        self.lines: deque[str] = deque(maxlen=INSTALL_LINES)
        self.progress: Optional[dict[str, Any]] = None
        self.model: Optional[dict[str, Any]] = None
        self.error: Optional[str] = None
        self.check: Optional[dict[str, bool]] = None
        self.port: Optional[int] = None
        # "unverified" when a download could not be checked against its checksum.
        self.note: Optional[str] = None
        # Seconds until the next try while Hugging Face rate-limits us.
        self.retry_in: Optional[int] = None
        # Set by the manager: point the provider at the server and tick its model.
        self.on_running: Optional[Callable[[str, str], None]] = None

    # -- reading -----------------------------------------------------------------------

    def busy(self) -> bool:
        return self.phase in ("installing", "downloading", "loading", "checking")

    def state(self) -> dict[str, Any]:
        settings = _load_settings()
        with self._lock:
            return {
                "phase": self.phase,
                "lines": list(self.lines),
                "progress": dict(self.progress) if self.progress else None,
                "model": dict(self.model) if self.model else None,
                "error": self.error,
                "check": dict(self.check) if self.check else None,
                "note": self.note,
                "retry_in": self.retry_in,
                "address": f"127.0.0.1:{self.port}" if self.port and self.phase == "running" else None,
                "autostart": bool(settings.get("autostart")),
                "last_choice": settings.get("choice"),
            }

    # -- install ------------------------------------------------------------------------

    def install(self) -> dict[str, Any]:
        brew = find_brew()
        if not brew:
            return {"ok": False, "error": "Homebrew is not installed"}
        return self._run("installing", lambda: self._install(brew))

    def _install(self, brew: str) -> None:
        env = {**os.environ, "HOMEBREW_NO_ENV_HINTS": "1", "NONINTERACTIVE": "1"}
        env["PATH"] = ":".join([*_BIN_DIRS, env.get("PATH", "")])
        proc = subprocess.Popen(
            [brew, "install", "llama.cpp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if self._cancel.is_set():
                proc.kill()
                raise _Cancelled()
            line = line.rstrip()
            if line:
                with self._lock:
                    self.lines.append(line[:200])
        proc.wait()
        if proc.returncode != 0 or not find_llama_server():
            last = self.lines[-1] if self.lines else f"brew exited with {proc.returncode}"
            raise RuntimeError(last)
        with self._lock:
            self.phase = "idle"

    # -- start --------------------------------------------------------------------------

    def start(self, choice: dict[str, Any]) -> dict[str, Any]:
        """Run a model: `{"kind": "suggested", "id": …}`, `{"kind": "ollama", "name": …}` or
        `{"kind": "file", "path": …}`. Stops the one running first."""
        try:
            plan = self._plan(choice)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return self._run("downloading" if plan.get("download") else "loading", lambda: self._start(choice, plan))

    def _plan(self, choice: dict[str, Any]) -> dict[str, Any]:
        kind = (choice or {}).get("kind")
        if kind == "suggested":
            entry = _catalog_entry(str(choice.get("id") or ""))
            if not entry:
                raise ValueError("unknown model")
            return {
                "name": entry["name"],
                "detail": entry.get("quant"),
                "alias": entry["id"],
                "download": entry,
                "spec_type": entry.get("spec_type"),
                "context_max": entry.get("context_max"),
            }
        if kind == "ollama":
            name = str(choice.get("name") or "")
            found = next((m for m in ollama_models() if m["name"] == name), None)
            if not found:
                raise ValueError(f"Ollama has no model named {name}")
            return {"name": name, "detail": "from Ollama", "alias": name, "file": found["path"]}
        if kind == "file":
            path = Path(str(choice.get("path") or "")).expanduser()
            if not path.is_file() or path.suffix.lower() != ".gguf":
                raise ValueError("choose a .gguf file")
            plan: dict[str, Any] = {"name": path.stem, "detail": "your file", "alias": path.stem, "file": str(path)}
            # A multi-token-prediction draft saved beside it (`mtp-<name>`), as ggml-org ships them.
            draft = path.with_name("mtp-" + path.name)
            if draft.is_file():
                plan.update(draft=str(draft), spec_type="draft-mtp", detail="your file + MTP")
            return plan
        raise ValueError("unknown choice")

    def _start(self, choice: dict[str, Any], plan: dict[str, Any]) -> None:
        server = find_llama_server()
        if not server:
            raise RuntimeError("llama.cpp is not installed")
        self._stop_process()
        with self._lock:
            self.model = {
                "name": plan["name"],
                "detail": plan.get("detail"),
                "alias": plan["alias"],
                "download": bool(plan.get("download")) and not _downloaded(plan["download"]),
            }
            self.check = None
        if plan.get("download"):
            plan = {**plan, **self._download(plan["download"])}
        with self._lock:
            self.phase = "loading"
            self.progress = None
        memory = local_machine.model_memory_bytes()
        rec = recommendation_for(plan["alias"])
        model_max = plan.get("context_max") or (rec.context_max if rec else None)
        context = local_machine.recommended_context(model_max, memory) or 32_768
        port = _pick_port()
        args = [server, "-m", plan["file"], "--alias", plan["alias"], "-c", str(context), "--jinja",
                "--host", "127.0.0.1", "--port", str(port)]
        if plan.get("draft"):
            args += ["--spec-type", plan.get("spec_type") or "draft-mtp", "--spec-draft-model", plan["draft"]]
        log = _state_path("llamacpp-server.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "wb") as fh:
            proc = subprocess.Popen(args, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        watcher = subprocess.Popen(
            ["/bin/sh", "-c", 'while kill -0 "$0" 2>/dev/null; do sleep 2; done; kill "$1" 2>/dev/null', str(os.getpid()), str(proc.pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        _state_path("llamacpp.pid").write_text(json.dumps({"pid": proc.pid, "watcher": watcher.pid}), encoding="utf-8")
        with self._lock:
            self._proc, self._watcher, self.port = proc, watcher, port
            self.model = {**(self.model or {}), "context": context, "file": _pretty(Path(plan["file"]))}
        self._wait_loaded(proc, port, log)
        with self._lock:
            self.phase = "checking"
        check = self._check(port, plan["alias"])
        with self._lock:
            self.check = check
        if not check.get("answers"):
            self._stop_process()
            raise RuntimeError("the model loaded but did not answer")
        settings = _load_settings()
        settings["choice"] = choice
        _save_settings(settings)
        if self.on_running:
            self.on_running(f"http://127.0.0.1:{port}", plan["alias"])
        with self._lock:
            self.phase = "running"
        threading.Thread(target=self._watch_exit, args=(proc, log), daemon=True).start()

    def _download(self, entry: dict[str, Any]) -> dict[str, Any]:
        wanted = [entry["file"], *([entry["draft"]] if entry.get("draft") else [])]
        have = {f: _cached_file(entry["repo"], f) for f in wanted}
        if all(have.values()):
            return {"file": str(have[entry["file"]]), "draft": str(have[entry["draft"]]) if entry.get("draft") else None}
        missing = [f for f in wanted if not have[f]]
        try:
            # Once, at the start: the commit, each file's size and its sha256 (the LFS id).
            commit, files = _hf_files(entry["repo"], self._rate_wait)
        except (httpx.HTTPError, KeyError, ValueError):
            # The API is not answering (down, or its own rate limit); the files come from
            # another address with its own limit, so download anyway, unverified.
            commit, files = "", {}
            for f in missing:
                c, info = _hf_resolve_info(entry["repo"], f, self._rate_wait)
                commit = commit or c
                files[f] = info
            with self._lock:
                self.note = "unverified"
        for f in missing:
            if f not in files:
                raise RuntimeError(f"{f} is no longer in {entry['repo']}")
        total = sum(files[f]["size"] for f in missing)
        free = _free_bytes(cache_dir())
        if free is not None and free < total:
            raise RuntimeError(f"not enough free space: {total // 10**9} GB needed, {free // 10**9} GB free")
        done_before = 0
        started = time.monotonic()
        for f in missing:
            blobs = _repo_dir(entry["repo"]) / "blobs"
            blobs.mkdir(parents=True, exist_ok=True)
            blob = blobs / files[f]["oid"]
            part = blob.with_name(blob.name + ".incomplete")
            size = files[f]["size"]
            if not (blob.is_file() and blob.stat().st_size == size):
                url = f"{HF}/{entry['repo']}/resolve/{commit}/{f}"
                for attempt in range(RATE_LIMIT_RETRIES + 1):
                    wait = self._fetch(url, part, size, done_before, total, started)
                    if wait is None:
                        break
                    if attempt == RATE_LIMIT_RETRIES:
                        raise RuntimeError("download failed: HTTP 429 (Hugging Face is rate-limiting downloads)")
                    self._rate_wait(wait)
                if part.stat().st_size != size:
                    raise RuntimeError("download incomplete; start again to resume")
                if files[f].get("sha256"):
                    with self._lock:
                        self.progress = {**(self.progress or {}), "done": done_before + size, "total": total, "verifying": True}
                    if _sha256(part, self._cancel) != files[f]["sha256"]:
                        part.unlink(missing_ok=True)
                        raise RuntimeError(f"{f} did not match its checksum and was removed; start again to download it afresh")
                part.rename(blob)
            done_before += size
            self._set_progress(done_before, total, started)
            have[f] = _place(entry["repo"], commit, f, blob)
        return {"file": str(have[entry["file"]]), "draft": str(have[entry["draft"]]) if entry.get("draft") else None}

    def _fetch(self, url: str, part: Path, size: int, done_before: int, total: int, started: float) -> Optional[int]:
        """Download `url` into `part`, resuming what is there. None when done; on a 429,
        the seconds Hugging Face asks us to wait."""
        have_bytes = part.stat().st_size if part.is_file() else 0
        headers = {"Range": f"bytes={have_bytes}-"} if have_bytes else {}
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as resp:
            if resp.status_code == 429:
                return _retry_after(resp)
            if resp.status_code == 200:
                have_bytes = 0
            elif resp.status_code != 206:
                raise RuntimeError(f"download failed: HTTP {resp.status_code}")
            with open(part, "ab" if have_bytes else "wb") as out:
                got = have_bytes
                last = 0.0
                for chunk in resp.iter_bytes(1 << 20):
                    if self._cancel.is_set():
                        raise _Cancelled()
                    out.write(chunk)
                    got += len(chunk)
                    now = time.monotonic()
                    if now - last > 0.5:
                        last = now
                        self._set_progress(done_before + got, total, started)
        return None

    def _rate_wait(self, seconds: int) -> None:
        """Wait out a 429, a second at a time so the card can count down and Cancel works."""
        end = time.monotonic() + seconds
        while True:
            left = int(end - time.monotonic() + 0.999)
            if left <= 0:
                break
            with self._lock:
                self.retry_in = left
            if self._cancel.wait(min(1.0, left)):
                with self._lock:
                    self.retry_in = None
                raise _Cancelled()
        with self._lock:
            self.retry_in = None

    def _set_progress(self, done: int, total: int, started: float) -> None:
        elapsed = max(time.monotonic() - started, 0.001)
        rate = done / elapsed if done else 0
        left = int((total - done) / rate) if rate > 0 else None
        with self._lock:
            self.progress = {"done": done, "total": total, "seconds_left": left}

    def _wait_loaded(self, proc: subprocess.Popen, port: int, log: Path) -> None:
        deadline = time.monotonic() + LOAD_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                self._stop_process()
                raise _Cancelled()
            if proc.poll() is not None:
                raise RuntimeError(_last_error(log) or f"llama-server exited with {proc.returncode}")
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        self._stop_process()
        raise RuntimeError("the model took too long to load")

    def _check(self, port: int, alias: str) -> dict[str, bool]:
        """One reply and one tool call, so a model that cannot drive the agent is caught
        here and not in the user's first session."""
        tool = {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "The current time in a city.",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            },
        }
        def ask(body: dict[str, Any]) -> Optional[dict[str, Any]]:
            try:
                r = httpx.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={"model": alias, "max_tokens": 1024, **body},
                    timeout=CHECK_TIMEOUT_S,
                )
                if r.status_code != 200:
                    return None
                return (r.json().get("choices") or [{}])[0].get("message") or {}
            except Exception:  # noqa: BLE001 - a failed check is reported, not raised
                return None

        reply = ask({"messages": [{"role": "user", "content": "Say hello in one word."}]})
        if reply is None:
            return {"answers": False, "tools": False}
        call = ask({"messages": [{"role": "user", "content": "What time is it in Paris? Use the tool."}], "tools": [tool]})
        return {"answers": True, "tools": bool(call and call.get("tool_calls"))}

    def _watch_exit(self, proc: subprocess.Popen, log: Path) -> None:
        proc.wait()
        with self._lock:
            if self._proc is not proc:
                return  # stopped or replaced on purpose
            self._proc = None
            self.phase = "failed"
            self.error = "llama-server stopped: " + (_last_error(log) or f"exit {proc.returncode}")

    # -- stop, cancel, autostart -------------------------------------------------------

    def stop(self) -> dict[str, Any]:
        self._cancel.set()
        self._stop_process()
        with self._lock:
            self.phase, self.progress, self.error = "idle", None, None
        return {"ok": True, **self.state()}

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        return {"ok": True}

    def _stop_process(self) -> None:
        with self._lock:
            proc, watcher = self._proc, self._watcher
            self._proc = self._watcher = None
        if watcher is not None:
            _end(watcher)
        if proc is not None:
            _end(proc)
        try:
            _state_path("llamacpp.pid").unlink()
        except OSError:
            pass

    def set_autostart(self, on: bool) -> dict[str, Any]:
        settings = _load_settings()
        settings["autostart"] = bool(on)
        _save_settings(settings)
        return {"ok": True, **self.state()}

    def on_app_start(self) -> None:
        """At server start: stop a llama-server an earlier run left behind, then start the
        last model if the user turned on "Start with OpenWorker"."""
        try:
            left = json.loads(_state_path("llamacpp.pid").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            left = None
        if isinstance(left, dict):
            pid = left.get("pid")
            if isinstance(pid, int) and _is_llama_server(pid):
                _kill(pid)
            try:
                _state_path("llamacpp.pid").unlink()
            except OSError:
                pass
        settings = _load_settings()
        if supported() and settings.get("autostart") and settings.get("choice") and find_llama_server():
            self.start(settings["choice"])

    def shutdown(self) -> None:
        self._cancel.set()
        self._stop_process()

    # -- the worker thread ----------------------------------------------------------------

    def _run(self, phase: str, work: Callable[[], None]) -> dict[str, Any]:
        with self._lock:
            if self.busy():
                return {"ok": False, "error": "busy"}
            self._cancel.clear()
            self.phase, self.error, self.progress = phase, None, None
            self.note = self.retry_in = None
            self.lines.clear()

        def body() -> None:
            try:
                work()
            except _Cancelled:
                self._stop_process()
                with self._lock:
                    self.phase, self.progress = "idle", None
            except Exception as e:  # noqa: BLE001 - shown on the card
                with self._lock:
                    self.phase, self.error = "failed", str(e) or type(e).__name__

        self._thread = threading.Thread(target=body, name="llamacpp-setup", daemon=True)
        self._thread.start()
        return {"ok": True, **self.state()}


def _last_error(log: Path) -> Optional[str]:
    """The most telling line of llama-server's log: the last error, else the last line."""
    try:
        lines = [l.strip() for l in log.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except OSError:
        return None
    errors = [l for l in lines if " E " in l or "error" in l.lower()]
    pick = (errors or lines or [None])[-1]
    return pick[:200] if pick else None


_runner: Optional[LlamaCppRunner] = None
_runner_lock = threading.Lock()


def runner() -> LlamaCppRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = LlamaCppRunner()
        return _runner


def pick_gguf_file() -> dict[str, Any]:
    """The Mac's file chooser, from the server (a web page cannot learn a file's path)."""
    if sys.platform != "darwin":
        return {"ok": False, "error": "Mac only"}
    script = 'POSIX path of (choose file with prompt "Choose a GGUF model file")'
    try:
        out = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to activate', "-e", script],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "no file chooser available"}
    path = (out.stdout or "").strip()
    if out.returncode != 0 or not path:
        return {"ok": False, "canceled": True}
    return {"ok": True, "path": path}
