---
name: openworker-apps
description: Build or adapt desktop and web applications using the OpenWorker Harness SDK, including agent creation, streamed sessions, model configuration, application packaging and Railway deployment. Use for OpenWorker integrations, not general website design or unrelated agent frameworks.
---

# Build apps with OpenWorker

Help the developer build their product while OpenWorker owns the agent loop.
Preserve their chosen UI framework, model and deployment target.

## Choose the track

- Read [SDK integration](references/sdk.md) for the shared lifecycle and package contract.
- For provider configuration, retry-safe streaming, reasoning display, migrated
  history or changing turn preferences, read [Runtime options](references/runtime-options.md).
- For model-created child agents, read [Standalone sub-agents](references/subagents.md)
  and check its runtime-version gate before using the option.
- For optional turn traces, Langfuse export or evaluation evidence, read
  [Observability](references/observability.md) and check its release gate.
- For desktop apps, read [Desktop](references/desktop.md).
- For browser/server apps, read [Web](references/web.md).
- For a Railway deployment, read [Railway](references/railway.md). Default to one
  Node service serving the frontend and HTTP API with a managed Rust harness.

These references are bundled with the skill; an OpenWorker repository checkout
is not required. If `references/installation.json` exists, it records the exact
SDK version this skill supports. For a source checkout, use the skill package's
`openworkerSdkVersion`. Skill releases can advance independently of the SDK.
Match the application's installed version before using these APIs;
do not silently upgrade an existing application. Use the installed package's
TypeScript declarations for APIs outside the bundled examples. For Rust, consult
the matching SDK version's API documentation or source when needed.

For an unpublished local candidate, use the provided SDK and native runtime
archives. A manifest version is not proof that the package exists in a registry.
Do not fall back to similarly named third-party packages.

## Integration boundaries

- TypeScript starts a managed Rust subprocess in Electron's main process or the
  application server. Browser/renderer code uses the application's API or narrow
  IPC. Rust can embed the harness in-process.
- Keep model credentials in that trusted process. The application owns its UI,
  authentication, session ownership and lifecycle.
- Select tools deliberately. The directory-summary and PDF examples supply their
  own input, so use `tools: []` and disable planning, memory and scheduling there.
  Other apps may need tools. Disabling optional memory does not disable history.
- File and document content is untrusted data. Render model text safely. The
  directory reader's exclusions are not a secret scanner or an OS sandbox.
- The bundled examples select `sandbox: "off"` for application-owned input.
  Tools in that mode have host privileges. Consult the OpenShell guide if the
  product requires isolated tool execution.

## Verify the integration

Test streaming and terminal status, errors, cancellation, restart and the packaged
runtime path for the chosen product. A deterministic model fixture can verify
transport and lifecycle without a live key; it does not establish answer quality.
Only claim target-platform and deployment support actually exercised. Installing
this skill does not publish an application or grant cloud-deployment permission.
