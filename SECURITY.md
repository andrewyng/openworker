# Security Policy

OpenWorker is a security-positioned project; we hold ourselves to the standard we
pitch. If you find a vulnerability, we want to hear about it.

## Reporting a vulnerability

Email **security@openworker.com** with:

- a description of the issue and its impact,
- reproduction steps or a proof of concept,
- the version you tested (app version from the About screen, or a commit hash).

Please use email rather than a public issue so a fix can ship before details are
public. We'll acknowledge your report within 3 business days, keep you updated as
we work on it, and credit you in the release notes when the fix ships (unless you
prefer otherwise). Please give us a reasonable window to fix before public
disclosure.

## Scope

- The desktop app and local agent server in this repository - including the
  permission gates, approval/reviewer flow, and audit trail. Bypasses of the
  human-only floors or approval gates (e.g. via prompt injection or a malicious
  MCP tool) are in scope and treated as high severity.
- The OAuth broker service used for managed connectors.

Out of scope: vulnerabilities in third-party model providers or connected
services themselves, and issues requiring an already-compromised machine.

## Supported versions

The latest release only. The app auto-updates, so fixes reach installs quickly -
this is also why we don't patch older versions.

There is no bug bounty program at this time.
