---
name: semgrep-review
description: Run a semgrep scan and turn findings into triaged, contextual fixes
---
Run a static-analysis pass with semgrep and own the findings end to end.

1. Check the tool: `semgrep --version`. If it's missing, ask for it with
   `request_tool("semgrep", …)` rather than skipping the pass. If the user declines,
   continue with a targeted manual review — read the routes/handlers, the auth and
   session code, every query built by string concatenation, deserialization, and
   outbound requests built from user input — and say in your report that the static
   pass was manual, so the user knows the coverage is narrower than a full scan.
   Note that community semgrep rules miss whole classes (e.g. SQL built through a
   project's own DB wrapper), so reading the code is worth doing even when it runs.
2. Scan the repo (from its root):
   `semgrep scan --config auto --json --quiet -o /tmp/semgrep.json`
   Use `--config auto` unless the repo carries its own rules (`.semgrep.yml`,
   `semgrep.yml`) — prefer the repo's own configuration when present.
3. Parse the JSON and triage EVERY finding — do not echo the raw report:
   - Read the flagged code and enough surrounding context to judge reachability.
   - Is the tainted input attacker-controlled or internal? Is there an upstream guard?
   - Rate: critical / high / medium / low / noise, with a one-line justification each.
4. Fix what's real, highest severity first:
   - Match the codebase's own conventions (its validation helpers, escaping utilities,
     parameterized-query style) — read neighboring code before writing the fix.
   - Add or extend a test that fails without the fix where the test harness makes that
     reasonable.
   - Group fixes by theme (one branch per theme), never one giant mixed diff.
5. For findings you judge noise, say WHY (e.g. constant input, dead code, framework
   already escapes) — never silently drop them, and never add ignore rules to make the
   scanner quiet without agreement.
6. Deliver: a short findings table (severity · location · verdict · action) and the
   fix branches/PRs. If the repo has no semgrep config, offer to commit a starter
   `.semgrep.yml` pinned to the rulesets that mattered here.
