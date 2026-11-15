# OpenWorker Harness SDK for TypeScript

Build a desktop app, web app or Node.js utility with an agent that can use your
tools and keep its conversation across restarts. Your app owns the interface;
OpenWorker manages the agent loop.

[SDK overview](../README.md) · [Desktop guide](../../docs/harness/guides/desktop.md) · [Web guide](../../docs/harness/guides/server.md) · [API declarations](src/index.ts)

## Install

```sh
npm install @openworkerai/harness-sdk
```

Requires Node.js 20+ and ESM. This documentation-only client release uses the
unchanged native runtime **0.1.0-beta.3**. npm selects the exact platform package;
keep optional dependencies enabled. No Rust compiler or Python installation is
needed. Run the SDK in Node, Electron's main process or your application server;
browser code calls your app's API or IPC.

## Run your first agent

Save as `agent.mjs`. Provide `OPENAI_API_KEY` and `OPENWORKER_MODEL` in your process
environment or a private env file. Use an exact model ID available to your account.

```typescript
import { resolve } from 'node:path';
import { Harness } from '@openworkerai/harness-sdk';

const model = process.env.OPENWORKER_MODEL;
if (!model) throw new Error('Set OPENWORKER_MODEL');
const harness = await Harness.start({
  stateDir: resolve('.openworker-state'), sandbox: 'off',
});
try {
  await harness.setModelProvider({
    name: 'OpenAI', api: 'responses',
    baseUrl: process.env.OPENWORKER_MODEL_URL ?? 'https://api.openai.com/v1',
    credentials: { apiKeyEnv: 'OPENAI_API_KEY', storage: 'memory' },
  });
  const agent = harness.createAgent({
    name: 'first-agent-v1', instructions: 'Give concise, helpful answers.',
    defaultModel: { provider: 'OpenAI', name: model },
    tools: [], planning: false, memory: false, scheduling: false,
  });
  const session = await agent.newSession();
  const result = await session.run('Suggest three names for a file-organizing app.');
  if (result.status !== 'completed') throw new Error(`Run ended: ${result.status}`);
  console.log(result.finalOutput);
} finally {
  await harness.shutdown();
}
```

```sh
node --env-file=/path/to/private.env agent.mjs
```

`OPENWORKER_MODEL_URL` optionally selects another Responses-compatible endpoint.

This text-only example exposes no tools and explicitly disables sandboxing.
When adding tools, choose their execution boundary deliberately. Node callbacks
retain your app's privileges; `workspace` alone does not confine filesystem access.
See [custom tools and scope](../../docs/harness/guides/tools.md).

## Build with your coding agent

```sh
npx @openworkerai/openworker-harness-builder --agent claude --project .
```

Use `--agent codex` for Codex or `--global` for user-wide installation. Existing
installs prompt before replacement; `--replace-existing` skips the prompt and
saves a backup. The Skill's `installation.json` records its tested SDK version.
See the [Skill guide](../../skills/README.md) and runnable
[file-organizer example](../../skills/openworker-harness-builder/references/local-script.md).

## Add the next capability

| You want to… | Start here |
| --- | --- |
| Add tools and show their arguments | [Tools and MCP](../../docs/harness/guides/tools.md) |
| Stream text and handle approval cards | [Streaming and approval loop](../../docs/harness/guides/streaming.md#streaming-with-tool-approvals) |
| Change models or refresh credentials | [Models and credentials](../../docs/harness/guides/providers.md) |
| Resume a conversation or delegate work | [Sessions and teams](../../docs/harness/guides/sessions.md) |
| Accept images, PDFs and text files | [Attachments](../../docs/harness/guides/attachments.md) |
| Isolate tools and inspect content | [OpenShell](../../docs/harness/guides/openshell.md) · [Guardrails](../../docs/harness/guides/guardrails.md) |
| Inspect turns and export traces | [Observability](../../docs/harness/guides/observability.md) |
| Run a shared service | [Deployment options](../../docs/harness/guides/deployment.md) |

## Integration contract

- Start one harness for the app/service lifecycle; always shut it down on exit.
- Use `createAgent` and `setModelProvider` for new apps. Explicit executables,
  startup agent arrays and `setConnection` are advanced interfaces.
- Agent definitions are immutable in durable state. Change the name when changing
  instructions, tools or approval mode. Identical registration is safe.
- Node resolves `apiKeyEnv` and sends the key over private stdio. `inheritEnv` is
  unnecessary for this credential path. Memory-only keys must be resupplied after
  app restart; the SDK does not load env files automatically.
- Save `session.reference()` and use `agent.resumeSession(reference)` for recovery.
  Stopping observation does not cancel work; use `session.cancel()` for Stop.
- Never automatically retry an action whose outcome is unknown. Reconcile durable
  state and the external system first.

See [versions and compatibility](../../docs/harness/guides/releases.md) and
[troubleshooting](../../docs/harness/guides/troubleshooting.md).
