---
id: github-github-list-commits-gateway-unreachable-fetch-failed-08-3
title: github github-list_commits gateway unreachable fetch failed 08-31
state: active
updated: '2026-09-01'
tags: []
---
**Now:** GitHub MCP gateway (`fetch failed`, error -32603) now unreachable across the FIFTH identical run: 08-27, 08-28, 08-31, and 2026-09-01 all returned identical `fetch failed` on all 10 iconbaypark2900 repos. (08-24 was `Bad credentials` — a different, reachable-gateway error.) The repo-activity brief has been "0/10 verifiable, unknown not no-activity" since 08-27. (source: /home/iconbaypark2900/OpenWorker/__task__task-717e4360ea/repo-activity-2026-09-01.md)

## History
- 2026-09-01 — GitHub MCP gateway returned identical `fetch failed` (-32603) on all 10 iconbaypark2900 repos for the FIFTH consecutive repo-activity-brief run (08-27, 08-28, 08-31, 09-01). The brief has been "0/10 verifiable, unknown not no-activity" since 08-27. (source: repo-activity-2026-09-01.md) (source: /home/iconbaypark2900/OpenWorker/__task__task-717e4360ea/repo-activity-2026-09-01.md)
- 2026-08-31 — GitHub MCP gateway (`github-list_commits` / the github server) returned `MCP error -32603: fetch failed` on all 10 iconbaypark2900 repos for four consecutive repo-activity-brief runs (08-27, 08-28, 08-31 identical; 08-24 was `Authentication Failed: Bad credentials` — a different error on a reachable gateway). This is a gateway-level outage (not a per-repo issue, not a shell token problem — shell still has no GITHUB_TOKEN/gh auth), so the brief has been reporting "0/10 verifiable, unknown not no-activity" since 08-27. (source: /home/iconbaypark2900/OpenWorker/__task__task-717e4360ea/repo-activity-2026-08-31.md)
