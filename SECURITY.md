# Security Policy

OpenWorker is a local-first desktop agent with access to user-approved files,
tools, credentials, and external services. Please report suspected
vulnerabilities privately so maintainers can investigate before public
disclosure.

## Supported versions

Security fixes are developed on the default branch and shipped in the newest
available release. Older beta builds may not receive backports; reproduce on
the latest release or current `main` when practical.

## Reporting a vulnerability

Use GitHub's **Security** tab and choose **Report a vulnerability** when that
option is available. Include:

- the affected release or commit and operating system;
- the component, connector, or approval boundary involved;
- a minimal reproduction or proof of concept;
- the expected and observed behavior;
- the potential impact and any known mitigations.

If private vulnerability reporting is unavailable, open a minimal issue asking
the maintainers for a private reporting channel. Do not include secrets,
personal data, working exploits, or other sensitive details in a public issue.

Please allow maintainers time to reproduce, assess, and coordinate a fix before
public disclosure. This policy does not promise a response or remediation
deadline.

## Scope notes

Reports are especially useful when they involve:

- approval-gated sends, writes, shell commands, or unattended automations;
- local secret storage, launch tokens, OAuth callbacks, or connector tokens;
- artifact and workspace path boundaries;
- desktop sidecar, webview, update, installer, or release integrity;
- prompt or tool input that crosses a trust boundary.

Model-provider behavior, third-party service outages, and social-engineering
requests without a product vulnerability are generally outside this policy,
but reports that demonstrate an OpenWorker control bypass remain in scope.
