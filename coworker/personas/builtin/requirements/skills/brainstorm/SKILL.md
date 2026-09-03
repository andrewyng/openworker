---
name: brainstorm
description: Structured, evidence-only interview that turns a raw idea into a spec — one topic at a time, TBD + Open Questions instead of invented detail
---
Expand a raw idea into a structured spec through a short interview — never by filling
gaps with plausible-sounding invention.

1. Get the idea: inline text, a tagged file, or ask for it if neither was given. Derive a
   short feature slug and a spec slug from its content; confirm both before writing
   anything (cheap to fix now, expensive after the file exists).
2. Detect complexity signals from the idea itself (don't ask about them yet) — external
   redirects/OAuth/payment, async/callback flows, multiple roles, entities with a status
   lifecycle, rate limits/quotas. These decide which extra topics you'll need later.
3. Interview one topic per message, 2-5 questions, wait for the reply:
   - Overview: what it does, whose pain, why now.
   - Users & access: roles, gating, entry point.
   - Core flow: user does X → system does Y → user sees Z, happy path only.
   - (Only if a complexity signal fired) Deep dive: business-level system actions,
     decision points, state transitions, what happens if the flow is interrupted mid-way.
   - Validation, limits & exact wording: required fields, the actual numbers, the actual
     error/success strings — not paraphrased, not defaulted.
   - System context: what business-level data gets stored, which external services are
     involved (name + purpose, not SDK/endpoint), what triggers a notification.
   - Edge cases & risks: what happens on disconnect/timeout/concurrent action; risks
     grounded in what was actually said, not invented compliance/infra scenarios.
4. A vague answer gets exactly one follow-up asking for the specific value. Still vague,
   or skipped → `<!-- TBD -->` in the doc plus an `OQ-N` Open Question. Never substitute
   an industry-typical number or wording to make a section look complete.
5. Before writing, run an evidence check on your own draft: every number and every exact
   string must trace to something the user said this session (or a file they tagged) —
   anything that doesn't, cut it or turn it into a TBD + OQ.
6. Preview the spec in plain language (what you captured, what's still TBD/open) and get
   a go-ahead before writing the file.
7. Write `docs/<feature>/brainstorms/<idea-slug>.md`. One feature can hold several
   brainstorm docs for different ideas — never merge two ideas into one file.
8. Resolve Open Questions one at a time right after writing: answer, defer ("hold"), or
   mark out of scope — update the doc for each; don't leave them buried for later.
9. Deliver: the finished spec's path, how many Open Questions remain open vs resolved,
   and the natural next step (a fuller requirements doc, or straight to implementation
   planning) — named, not started.
