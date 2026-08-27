#!/usr/bin/env python3
"""Read-only sweep: last-24h commits for the 10 brief repos. Per-item fail tolerant."""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

CUTOFF = datetime.now(timezone.utc) - timedelta(hours=24)
cutoff = CUTOFF.strftime("%Y-%m-%dT%H:%M:%SZ")
REPOS = [
    "dcode-stack", "workstation-stack", "ragtradesystem", "sigma",
    "liaison-agentSystem", "qgg_research", "materialScience", "setup",
    "polymarket_btc", "sourcelab_ai_production_scaffold",
]
token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "repo-ops-brief"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read().decode()


print(f"cutoff_utc={cutoff} token={'yes' if token else 'no'}")
for r in REPOS:
    base = f"https://api.github.com/repos/iconbaypark2900/{r}"
    try:
        status, body = fetch(base + "/commits?since=" + cutoff + "&per_page=10")
        data = json.loads(body)
        if isinstance(data, dict):
            print(f"{r}: API-ERROR status={status} msg={data.get('message')}")
            continue
        if not data:
            s2, body2 = fetch(base + "/commits?per_page=1")
            d2 = json.loads(body2)
            if isinstance(d2, list) and d2:
                c = d2[0]
                first = c["commit"]["message"].splitlines()[0]
                print(f"{r}: 0 commits in 24h | last: "
                      f"{c['commit']['committer']['date']} | {first!r}")
            else:
                print(f"{r}: 0 commits in 24h | last commit UNKNOWN")
        else:
            print(f"{r}: {len(data)} commit(s) in last 24h")
            for c in data:
                first = c["commit"]["message"].splitlines()[0]
                print(f"  {c['sha'][:7]} {c['commit']['committer']['date']} "
                      f"author={c['commit']['author']['name']} | {first!r}")
    except urllib.error.HTTPError as e:
        snip = e.read()[:120]
        print(f"{r}: HTTP {e.code} body={snip!r}")
    except Exception as e:
        print(f"{r}: EXC {type(e).__name__}: {e}")
