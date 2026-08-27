#!/usr/bin/env python3
"""Fetch the 6 most recent commits per repo and print date+subject.

Used for the 24h activity brief + stalled-repo detection (needs the
two latest commits per repo to see how long ago activity stopped).
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

REPOS = [
    "dcode-stack",
    "workstation-stack",
    "ragtradesystem",
    "sigma",
    "liaison-agentSystem",
    "qgg_research",
    "materialScience",
    "setup",
    "polymarket_btc",
    "sourcelab_ai_production_scaffold",
]
OWNER = "iconbaypark2900"

token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

def get(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "activity-brief",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def main():
    for repo in REPOS:
        url = f"https://api.github.com/repos/{OWNER}/{repo}/commits?per_page=6"
        try:
            data = get(url)
        except urllib.error.HTTPError as e:
            print(f"### {repo}\nERROR {e.code} {e.reason}")
            continue
        except Exception as e:
            print(f"### {repo}\nERROR {e!r}")
            continue
        print(f"### {repo}")
        if not data:
            print("  (no commits — empty repo?)")
            continue
        for c in data:
            sha = c.get("sha", "?")[:8]
            msg = (c.get("commit", {}).get("message") or "").splitlines()
            subject = msg[0].strip() if msg else ""
            author = (c.get("commit", {}).get("author") or {}).get("date", "")
            print(f"  {author}\t{sha}\t{subject}")
    # rate limit status for the record
    try:
        rl = get("https://api.github.com/rate_limit")
        rem = rl["resources"]["core"]["remaining"]
        print(f"### rate-limit-remaining={rem}")
    except Exception:
        pass

main()
