---
name: secret-scan
description: Hunt committed secrets with gitleaks and drive safe rotation
---
Find committed credentials and get them rotated and removed — without ever exposing them
further yourself.

ABSOLUTE RULE: never print a secret's value — not in output, notes, todo items, commits,
or PRs. Refer to every hit as "<kind> in <file>:<line> (commit <short-sha>)".

1. Check the tool: `gitleaks version`. If it's missing, do NOT skip this scan and do not
   stop the review — ask for it with `request_tool("gitleaks", …)`. If the user declines,
   or no pinned build exists for their platform, fall back to step 2b and say in your
   report that the sweep was manual.
2. Scan working tree AND history — history matters most: a secret deleted in HEAD is still
   live in every clone, and it is the hit users are most surprised by.
   a. With gitleaks:
      `gitleaks detect --source . --report-format json --report-path /tmp/gitleaks.json`
   b. Without it, do the same job by hand, and say so:
      - working tree: `git grep -nIE '(api[_-]?key|secret|token|password|BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}|sk_(live|test)_[0-9a-zA-Z]{16,}|xox[baprs]-)'`
      - history, including files deleted since: `git log -p --all -S 'AKIA' --pickaxe-all`
        and `git log --diff-filter=D --name-only --pretty=format:%h -- '*.env*' '*credential*' '*secret*'`,
        then read the removed contents with `git show <sha>^:<path>`.
      - Pipe anything you read through a redactor rather than into your transcript, e.g.
        `sed -E "s/[A-Za-z0-9_\\-]{16,}/[REDACTED]/g"` — the no-printing rule still applies.
3. Triage each hit by reading its context:
   - Real credential, test fixture, or example placeholder? Say which and why.
   - For real ones: what does it grant access to, and is it plausibly still valid?
4. For every real secret, in this order:
   a. ROTATE first — tell the user exactly where to revoke/rotate it (the provider's
      console page or CLI command). Rotation beats removal: history rewrite without
      rotation is false comfort.
   b. Remove it from the code: move to env vars or the project's secret store, matching
      how this codebase already handles configuration.
   c. Prevent recurrence: add/extend `.gitignore` for local secret files and offer a
      `.gitleaks.toml` baseline plus a pre-commit hook.
   d. History purge (git filter-repo/BFG) is DESTRUCTIVE and rewrites shared history —
      describe the trade-off and only proceed if the user explicitly asks.
5. Deliver: a hit list (kind · location · verdict · rotation status), the cleanup
   branch/PR, and the prevention setup you added or recommend.
