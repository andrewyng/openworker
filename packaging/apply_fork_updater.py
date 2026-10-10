#!/usr/bin/env python3
"""Keep this fork's Tauri updater config pointed at our own Releases.

Upstream merges often restore download.openworker.com / the official pubkey.
Call this after every sync (and from CI) so auto-update never redirects users
back to the English upstream channel.

Usage:
    python3 packaging/apply_fork_updater.py
    python3 packaging/apply_fork_updater.py --version 0.2.2-zh.1
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONF = ROOT / "surfaces" / "gui" / "src-tauri" / "tauri.conf.json"

# Community Chinese fork — must stay in lockstep with docs/FORK-MAINTENANCE.md.
FORK_OWNER_REPO = "cr-yijieshusheng/openworker"
ENDPOINTS = [
    f"https://github.com/{FORK_OWNER_REPO}/releases/latest/download/latest.json",
]
# Public half of the fork's minisign keypair (private key lives only in
# GitHub Actions secret TAURI_SIGNING_PRIVATE_KEY / local .secrets/).
PUBKEY = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEIyNUE4Q0I0NzFEQzM0RjMK"
    "UldUek5OeHh0SXhhc2w2c2pYWlBpVEFtT284R2lTNmlzUktXTG5PSFlBanVJQnFGcFdZUVBkY2wK"
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--version",
        help="optional semver to write into tauri.conf.json (e.g. 0.2.1-zh.3)",
    )
    args = ap.parse_args()

    data = json.loads(CONF.read_text())
    if args.version:
        data["version"] = args.version

    plugins = data.setdefault("plugins", {})
    plugins["updater"] = {
        "endpoints": ENDPOINTS,
        "pubkey": PUBKEY,
        "windows": {"installMode": "passive"},
    }
    CONF.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {CONF.relative_to(ROOT)} version={data['version']}")
    print(f"updater endpoint: {ENDPOINTS[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
