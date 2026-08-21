"""Contract tests for the Tauri webview Content Security Policy (#99).

CI does not boot a Tauri webview, so these hermetic assertions pin the policy that
ships in tauri.conf.json — and the artifact-iframe sandbox that actually closes
the XSS → local-API path the issue describes. A missing directive or a regression
to `csp: null` / `allow-same-origin` fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI_CONF = ROOT / "surfaces" / "gui" / "src-tauri" / "tauri.conf.json"
RIGHT_RAIL = ROOT / "surfaces" / "gui" / "src" / "components" / "RightRail.tsx"


def _security() -> dict:
    cfg = json.loads(TAURI_CONF.read_text())
    return cfg["app"]["security"]


def test_production_csp_is_enabled_and_covers_the_sidecar():
    sec = _security()
    csp = sec.get("csp")
    assert csp is not None and isinstance(csp, dict), "production CSP must not be null"

    # Tauri IPC + the dynamically-chosen sidecar port (127.0.0.1:0 → free_port()).
    connect = csp["connect-src"]
    for required in ("ipc:", "http://ipc.localhost", "http://127.0.0.1:*", "ws://127.0.0.1:*"):
        assert required in connect, f"connect-src missing {required!r}: {connect}"

    # No remote connect/script: CSP's real win is blocking XSS from talking to the internet
    # or loading remote script. The updater is a native Rust plugin — not webview connect-src.
    assert "https://" not in connect
    assert "unsafe-eval" not in csp.get("script-src", "")

    # React inline styles + pdfjs worker + attachment data:/blob: URLs.
    assert "unsafe-inline" in csp["style-src"]
    assert "blob:" in csp["worker-src"]
    assert "data:" in csp["img-src"] and "blob:" in csp["img-src"]
    assert csp["object-src"] == "'none'"


def test_dev_csp_is_set_and_allows_vite_hmr():
    # Without a separate devCsp, Tauri injects the prod CSP into `tauri dev` too —
    # and Vite HMR on localhost:1420 would be blocked.
    sec = _security()
    dev = sec.get("devCsp")
    assert dev is not None and isinstance(dev, dict)

    connect = dev["connect-src"]
    for required in (
        "ipc:",
        "http://127.0.0.1:*",
        "ws://127.0.0.1:*",
        "http://localhost:1420",
        "ws://localhost:1420",
    ):
        assert required in connect, f"devCsp connect-src missing {required!r}: {connect}"
    # HMR / Fast Refresh commonly needs eval in dev; prod must stay clean (pinned above).
    assert "unsafe-eval" in dev["script-src"]


def test_html_artifact_iframe_is_not_same_origin():
    """Parent CSP does not contain a srcDoc iframe. allow-same-origin + allow-scripts
    together let agent HTML reach window.parent.__COWORKER_API_TOKEN__. Scripts-only
    keeps interactive previews while giving the frame a unique opaque origin.
    """
    src = RIGHT_RAIL.read_text()
    # The HTML artifact preview is the only iframe that feeds agent-authored markup
    # through srcDoc — pin its sandbox specifically.
    match = re.search(
        r"<iframe\b[^>]*?\bsandbox=\"([^\"]+)\"[^>]*?\bsrcDoc=\{content\.content",
        src,
        re.DOTALL,
    )
    assert match, "RightRail HTML artifact iframe (sandbox + srcDoc) not found"
    tokens = match.group(1).split()
    assert "allow-scripts" in tokens
    assert "allow-same-origin" not in tokens, (
        "allow-same-origin on a scriptable artifact iframe re-opens the XSS → API path"
    )
