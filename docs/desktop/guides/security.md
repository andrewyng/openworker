# Security coworkers

Investigate code, dependencies and infrastructure with coworkers configured for security work.

These specialists help run scanners, interpret findings in context and prepare changes for review. They complement security expertise; a clean scan or an agent's conclusion is not a guarantee that a system is secure.

## Choose the investigation

In **Settings → Coworkers**, review the Security group and enable the specialist you need. Start a new conversation with the relevant repository folder and the minimum access required.

| Coworker | Starting point | Typical result |
|---|---|---|
| Security Coworker | Application source and secret-scanning findings | Prioritized code findings and focused fixes for review |
| Dependency Audit Coworker | Dependency manifests and lockfiles | A dependency-risk assessment and minimal upgrade proposals |
| Cloud Posture Coworker | Infrastructure-as-code and, when explicitly connected, cloud read access | Evidence-backed configuration findings and proposed IaC changes |

These are the currently documented specialist roles. Availability depends on your installation; additional experimental team roles are not assumed here.

## Start with findings, not automatic remediation

```text
Review this repository for application-security risks. Run suitable
scanners if available, then triage the findings against the code.
Explain the evidence and likely impact. Don't change files, push
branches or access a live cloud account yet.
```

For dependencies, ask for the smallest upgrade that addresses the advisory and the tests needed to verify it. For cloud posture, begin with local IaC and add explicitly scoped, read-only cloud access only when needed. The Cloud Posture role is instructed to propose changes in IaC rather than apply them to live resources; use read-only credentials to enforce that boundary too.

## Review the evidence

A useful finding names the affected file or resource, the scanner or other evidence, why it matters in this project and what remains uncertain. Ask the coworker to distinguish confirmed issues from hypotheses and accepted configuration choices.

Before accepting a fix, review the diff and project tests. Don't disable a scanner rule merely to make a report look clean. Publishing a fix branch or PR is a separate action from preparing it locally.

## Control access

Use [Ask for approval](approvals.md), a task-specific folder and appropriately scoped connectors or cloud credentials. Scanner installation and execution may need network access in your [sandbox](sandbox.md).

Never paste live secrets into a prompt or a report. Findings can themselves reveal sensitive paths, code or infrastructure details; review [privacy](privacy.md) before exporting or sharing them.
