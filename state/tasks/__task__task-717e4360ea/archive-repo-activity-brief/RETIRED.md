# Repo activity brief - retired 2026-09-02

Folded into the weekly CI sweep (task-f471043f3e). Its git-activity gathering now
runs deterministically in ~/.local/bin/ci-sweep-stage, which writes repo-activity.md
into the CI sweep's workspace every Saturday at 03:45.

## Why it was retired

It asked the GitHub MCP for commits on iconbaypark2900/*. Two structural faults:

1. Origin for this work is the self-hosted Forgejo at git.home.arpa:2222, and the
   github.com copies are stale mirrors - dcode-stack's was 11 days behind and agpack
   has no GitHub repo at all. So it reported mirror state, not work.
2. The gateway's GitHub token died. Its last five runs obtained no primary data at
   all - four on `fetch failed`, then `Bad credentials` on all 10 repos - while
   still finishing with status `ok`.

Measured lifetime: 22 runs, 9 ok / 10 incomplete / 3 aborted. Longest run 56 hours.
The last five `ok` runs produced reports of 230-1718 chars whose content was an
account of not being able to fetch anything.

Task definition and full run history preserved alongside this file.
Prior outputs remain in the parent directory.
