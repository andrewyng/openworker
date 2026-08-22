# Security policy

OpenWorker is a local-first desktop agent that can read and write files, run commands, use connected services, and send requests to model providers. It is still in beta, so responsible security reports are especially valuable.

This policy explains how to report a vulnerability in OpenWorker, its desktop surfaces, the local agent server, connectors, or the supporting authentication flows.

## Reporting a vulnerability

Please do not report a security vulnerability in a public GitHub issue, discussion, pull request, or commit.

Use GitHub's private vulnerability reporting flow from the repository's **Security** tab. If the **Report a vulnerability** option is not available, contact the maintainers privately through GitHub before opening a public issue. Please do not put exploit details, credentials, or private user data in a public discussion.

If you are unsure whether something is security-sensitive, treat it as private first. The maintainers can help classify the report.

## What is useful in a report

Please include as much of the following as you can, after removing secrets and personal data:

- a concise description of the vulnerability and the security boundary it crosses;
- the affected component, such as the GUI, local server/API, workspace and file tools, shell execution, connector, MCP integration, OAuth flow, updater, or release packaging;
- the affected version, commit, operating system, and installation method;
- clear reproduction steps and a minimal proof of concept, if one can be shared safely;
- the configured permission mode (`discuss`, `plan`, `interactive`, `custom`, or `auto`), if relevant;
- whether the workspace was trusted and whether a workspace `.coworker/config.toml` was involved;
- the connected provider, connector, or MCP server involved, without including its token or private data;
- the expected result, actual result, and security impact; and
- whether you believe the issue is exploitable remotely, by another local process, through a malicious workspace, through a browser page, or only by the current user.

For reports involving the local server, mention whether it was started by the desktop app or with `openworker-server`, and whether it used a custom host, port, or `COWORKER_API_TOKEN`. For reports involving OAuth or connectors, include the flow and callback endpoint involved, but redact authorization codes, refresh tokens, access tokens, cookies, and account identifiers.

## Security-relevant areas in this repository

The following details are included to make reports easier to reproduce and triage:

- The agent server normally binds to loopback. API requests are protected by the per-launch `X-OpenWorker-Token` when a token is configured, and browser origins are restricted to the desktop webview and local development origins. The standalone server writes its token to a user-only `sidecar-<port>.token` file; the desktop app supplies its launch token in memory.
- Filesystem writes are scoped to configured writable roots. The permission engine has read-only modes, approval-gated consequential tools, command allowlists, and workspace trust decisions. A workspace's command allowances are intended to take effect only after that canonical workspace has been trusted.
- Connector, provider, and MCP credentials are managed through `SecretStore`, not passed as model context. On macOS and Linux the store uses a user configuration directory with user-only permissions; Windows uses user ACLs where available. Environment-variable references and a local `.env` file may also be used.
- Remote MCP OAuth uses a loopback callback and state checking. Interactive browser authorization is intended to happen only through an explicit connect action; background turns should not silently open a browser.
- Relevant connector and tool activity is recorded in a local audit database with truncation and redaction for common credential, body, and browser-input fields. Audit entries can still contain paths, resource identifiers, and other operational metadata, so sanitize them before sharing.

These controls reduce risk but are not a guarantee that every integration, model provider, operating-system permission, or local configuration is safe. A report showing a way to bypass one of these boundaries is welcome.

## Do not include secrets

Never include API keys, OAuth codes, refresh or access tokens, cookies, passwords, private connector content, model-provider credentials, signing material, or unredacted logs in a report.

If a secret was exposed, revoke or rotate it immediately, then report the exposure with a description of what was affected and when. If you need to share a log or audit entry, replace values with placeholders such as `<redacted>` and review file paths, email addresses, workspace names, URLs, and message bodies for personal or confidential information.

## Coordinated disclosure

The maintainers will acknowledge reports when they can, investigate the affected code and impact, and coordinate a fix or mitigation. Please allow reasonable time for users to update before publishing technical details. We will work with the reporter on the disclosure timeline and credit them when they are comfortable being credited.

## Scope notes

This policy covers the OpenWorker source tree and official builds maintained from this repository, including the Python backend, desktop GUI/Tauri surface, speech-to-text sidecar, connectors, MCP support, authentication flows, and release packaging.

Expected model limitations, ordinary bugs with no security impact, and feature requests are generally not security vulnerabilities. However, model behavior or prompt-injection issues that can cause an unintended file write, command execution, credential disclosure, unauthorized connector action, or bypass of an approval boundary should be reported privately.

Thank you for helping keep OpenWorker, its contributors, and its users safe.
