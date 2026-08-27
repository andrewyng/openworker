#!/usr/bin/env python3
"""Read-only check of iconbaypark2900 repos: commits in last 24h + last-commit age.

Per-item failures are printed and do not abort the run. Stdlib only.
"""
import json
import urllib.request
import urllib.error
import datetime

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
NOW = datetime.datetime.now(datetime.timezone.utc)
SINCE_24 = NOW - datetime.timedelta(hours=24)


def get(url):
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "repo-ops-brief"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:  # noqa: BLE001 - surface network errors per-item
        return 0, str(e)


def parsed_date(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


for repo in REPOS:
    url = f"https://api.github.com/repos/{OWNER}/{repo}/commits?since={SINCE_24.strftime('%Y-%m-%dT%H:%M:%SZ')}&per_page=30"
    status, commits = get(url)
    print(f"=== {repo} status={status}")
    if status != 200:
        continue
    print(f"last24h={len(commits) if isinstance(commits, list) else 'unexpected'}")
    if not isinstance(commits, list):
        continue
    for c in commits[:30]:
        msg = (c["commit"]["message"].splitlines() or ["?"])[0]
        author = (c["commit"]["author"] or {}).get("date", "?")
        print(f"  {author} | {c['sha'][:7]} | {msg}")
    if commits:
        continue
    st2, last = get(f"https://api.github.com/repos/{OWNER}/{repo}/commits?per_page=1")
    if st2 == 200 and isinstance(last, list) and last:
        d = last[0]["commit"]["author"]["date"]
        days = (NOW - parsed_date(d)).days
        msg = (last[0]["commit"]["message"].splitlines() or ["?"])[0]
        print(f"  last-commit: {d} ({days} days ago) | {msg}")
    else:
        print(f"  last-commit: fetch status={st2} body={last!r}" if status == 200 else "n/a")
print("=== DONE ===")
