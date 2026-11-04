---
name: openworker-apps
description: Build or adapt desktop and web applications using the OpenWorker Harness SDK, including agent creation, streamed sessions, model configuration and application packaging. Use for OpenWorker integrations, not general website design or unrelated agent frameworks.
---

# Build apps with OpenWorker

Help the developer build their product while OpenWorker owns the agent loop. Explain the important SDK calls as you integrate them. Preserve the developer's chosen UI framework, model and deployment target.

## Find the matching implementation

Use the developer's OpenWorker checkout and pinned SDK version. If installed by our project installer, `references/checkout.json` records the source checkout and commit. It is a locator, not permission to edit that checkout or a reason to upgrade dependencies. Verify it still exists and compare its revision with the application's dependency pin before using its APIs.

If the checkout is unavailable, locate the matching source in [andrewyng/openworker](https://github.com/andrewyng/openworker/tree/openworker-harness-sdk). Use the application's pinned revision when one exists. For a new integration, resolve and record a commit before depending on the moving development branch. If neither local source nor remote access is available, ask for the matching checkout rather than inventing APIs.

Read the relevant guide and source before editing:

- Desktop: [desktop reference](references/desktop.md).
- Web: [web reference](references/web.md).
- Shared implementation: `harness/examples/quickstart/src/agent.ts` and the example `README.md` in that checkout.
- Package availability and compatibility: `docs/harness/SDK-RELEASE-CONTRACT.md` and `harness/release/manifest.json`. Check these before prescribing npm/Cargo registry commands; a version in a manifest is not proof it has been published.

## Integration decisions

- Start the harness once at the application boundary and shut it down on exit. TypeScript uses a managed Rust subprocess; Rust can embed the harness. Browser code never starts the Rust executable directly.
- Configure a model provider separately from the agent. Keep credentials in the trusted application process and outside renderer bundles, agent instructions and checked-in files. Ask for an environment-variable name or secret source, not a key in chat.
- Create an agent with instructions, a default model and an explicit tool list. A session owns conversation history; a run is the work started by sending a message. Stream events and also inspect the terminal result. Cancellation, errors and pending input are distinct from successful completion.
- The two reference apps collect content themselves. Keep their agents tool-free (`tools: []`, with planning, memory and scheduling disabled) unless the requested product needs tools. Disabling optional memory does not disable session history.
- Uploaded documents and source files are untrusted data, not instructions. Render responses as text or sanitized content. Do not treat a system prompt as a security boundary.
- The examples explicitly use host execution (`sandbox: "off"`); do not present them as sandboxed. If tools need isolation, read `docs/harness/guides/openshell.md`: the current OpenShell integration isolates ToolRunner while the harness coordinates outside it.

## Implement and verify

Adapt the maintained example rather than copying snippets into an unrelated skeleton. When copying an example outside the checkout, update local dependency paths and confirm the matching runtime helper can be found. Keep application UI and input handling separate from SDK lifecycle code.

Run the relevant example checks and the application's own build. Use the deterministic model fixture for transport/streaming tests when no live provider is configured, and label that qualification accurately. Check the visible UI if computer/browser tools are available. Report what ran, what remains unverified, and how to start the app. A local build does not qualify another OS, a cloud deployment or model answer quality.

Prepare packaging/deployment files appropriate to the requested target. Creating an app does not by itself authorize cloud provisioning, publication, credential creation or signing with a developer's identity.
