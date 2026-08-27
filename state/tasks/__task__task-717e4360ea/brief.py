import json, os, sys, time, urllib.request

TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
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

def get(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "repo-brief",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

now = time.time()
out = {}
for repo in REPOS:
    entry = {"repo": repo, "recent": [], "latest": None, "error": None}
    try:
        recent = get(f"https://api.github.com/repos/{repo}/commits?since=2026-08-17T00:00:00Z&per_page=30")
        for c in recent:
            entry["recent"].append({
                "sha": c["sha"][:8],
                "date": c["commit"]["committer"]["date"],
                "author": (c.get("author") or {}).get("login") or c["commit"]["author"]["name"],
                "msg": c["commit"]["message"].split("\n")[0],
            })
    except Exception as e:
        entry["recent_error"] = str(e)
    try:
        latest = get(f"https://api.github.com/repos/{repo}/commits?per_page=1")
        lc = latest[0]
        entry["latest"] = {
            "sha": lc["sha"][:8],
            "date": lc["commit"]["committer"]["date"],
            "msg": lc["commit"]["message"].split("\n")[0],
        }
    except Exception as e:
        entry["latest_error"] = str(e)
    out[repo] = entry

print(json.dumps(out, indent=1))
