---
name: threat-model
description: Structured STRIDE threat model for a system or feature before it ships — trust boundaries, threats, mitigations
---
Model the threats in a system or feature BEFORE code exists to scan — this is a
design-time skill, not a scanner pass.

1. Establish scope by reading what's already there: architecture docs, README, entry
   points (routes/handlers), and how data actually moves — don't ask the user to restate
   what the repo already shows. Ask only what the code can't tell you: trust boundaries
   a diagram doesn't capture, which data is sensitive (PII, credentials, payment), and
   what's still being designed vs already built.
2. Map the data flow: source → each hop → storage, marking every trust-boundary crossing
   (internet ↔ DMZ ↔ internal, user ↔ service, service ↔ third party).
3. For each component and each boundary crossing, run STRIDE — Spoofing, Tampering,
   Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege. Skip a
   category outright rather than force a threat that doesn't apply; "N/A — no external
   input on this path" beats an invented finding.
4. Score every real threat: likelihood (1-3) × impact (1-3). 7-9 blocks the design as-is;
   4-6 must land before this ships; 2-3 gets scheduled; 1 is accepted and documented, not
   silently dropped.
5. Every threat scoring 4+ needs a concrete mitigation tied to a real control ("JWT
   validation + refresh-token rotation at the gateway", not "add authentication"). If the
   mitigation already exists, cite it (file:line); if it's still TODO, say exactly what
   implementing it involves.
6. Deliver: a threat table (component · threat · score · mitigation · status) ordered by
   score, plus the trust-boundary diagram (ASCII or mermaid) you built it from. Offer to
   write it to `docs/security/threat-model-<slug>.md` if the repo tracks docs that way;
   otherwise leave it in chat.
7. Hand off explicitly: any threat scored 7-9 with no mitigation yet is the top-priority
   input to `semgrep-review` / `security-fix-pr` once the code exists — say so, rather
   than letting the threat model become a report nobody acts on.
