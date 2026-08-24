---
name: security-fix-pr
description: Turn triaged security findings into focused, reviewable fix PRs
---
Package security fixes so a busy reviewer can approve them with confidence.

1. One PR per theme (e.g. "parameterize SQL in the reports module"), never a mixed
   security dump. Small diffs get reviewed; big ones get postponed.
2. Branch naming: `security/<theme>` from the repo's default branch. Follow the repo's
   existing commit-message style.
3. Every fix commit carries its test: add or extend one that fails without the fix,
   in the repo's existing test layout and idiom. If testing a fix isn't practical,
   say so in the PR body instead of skipping silently.
4. PR body structure (keep it tight):
   - What was wrong, in plain language, with severity and why it matters HERE (one or
     two sentences of reachability/impact, not scanner boilerplate).
   - What the fix does, and what it deliberately does not change.
   - How it was verified (test names, commands run).
   - NEVER include secret values, exploit payloads, or step-by-step attack recipes in
     a public PR — describe the class of issue instead.
5. If the GitHub connector is available, open the PR with it; otherwise prepare the
   branch and hand the user the exact push/PR commands.
6. Fixing is yours; MERGING is the team's. Never merge your own security PR — deliver
   it and summarize what a reviewer should scrutinize.
