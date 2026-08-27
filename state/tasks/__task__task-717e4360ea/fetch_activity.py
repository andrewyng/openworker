#!/usr/bin/env python3
"""Fetch recent commit activity for a list of GitHub repos in one pass."""
import json
import subprocess
import urllib.request
import urllib.error
import datetime
import sys

REPOS = [
    "iconbaypark2900/dcode-stack",
    "iconbaypark2900/workstation-stack",
    "iconbaypark2900/ragtradesystem",
    "iconbaypark2900/sigma",
    "iconbaypark2900/liaison-agentSystem",
    "iconbaypark2900/qgg_research",
    "iconbaypark2900/materialScience",
    "iconbaypark2900/setup",
    "iconbaypark2900/polymarket_btc",
    "iconbaypark2900/sourcelab_ai_production_scaffold",
]

now = datetime.datetime.now(datetime.timezone.utc)
since24 = (now - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
since7 = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

TOKEN = None
try:
    TOKEN = subprocess.check_output(
        ["gh", "auth", "token"], stderr=subprocess.DEVNULL, timeout=10
    ).decode().strip()
except Exception:
    pass


def api_get(url):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "repo-activity-brief",
        },
    )
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            body = {"error": str(e)}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


def fmt_commit(c):
    out = [
        "- sha: %s" % c.get("sha", "")[:10],
        "  date: %s" % (c.get("commit", {}).get("author", {}).get("date", "?")),
        "  author: %s" % ((c.get("commit", {}).get("author", {}).get("name", "?")) or "?"),
        "  subject: %s" % (c.get("commit", {}).get("message", "").splitlines()[0] if c.get("commit", {}).get("message") else "?"),
    ]
    full = (c.get("commit", {}).get("message") or "").strip()
    if "\n" in full:
        out.append("  body: " + full.split("\n", 1)[1].strip()[:400].replace("\n", " | "))
    return "\n".join(out)


for repo in REPOS:
    print("=== %s ===" % repo)
    # Most recent commit (always, for stall detection)
    status, latest = api_get(
        "https://api.github.com/repos/%s/commits?per_page=1" % repo
    )
    if status != 200 or not isinstance(latest, list):
        print("ERROR status=%s body=%s" % (status, str(latest)[:200]))
        continue
    last_date = latest[0]["commit"]["author"]["date"] if latest else "never"
    print("last_commit: %s" % last_date)
    stale = (
        "STALLED(>7d)"
        if last_date and last_date < since7
        else "recent"
    )
    print("staleness: %s" % stale)
    # Commits in last 24h
    status, commits = api_get(
        "https://api.github.com/repos/%s/commits?since=%s&per_page=100" % (repo, since24)
    )
    if status != 200 or not isinstance(commits, list):
        print("commits_24h: ERROR status=%s body=%s" % (status, str(commits)[:200]))
        continue
    print("commits_24h: %d" % len(commits))
    for c in commits:
        print(fmt_commit(c))
    print()
