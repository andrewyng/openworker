# Intel IMA adapter for managed Node applications

This optional adapter starts an authenticated loopback HTTP listener in the app.
The Rust host sends ordinary inference requests to that listener. The adapter
translates them for IMA and checks its routing receipts. Use a stable local port
when reusing a durable state directory because the SDK pins the provider endpoint.

```typescript
import { attachIntelIma } from "@openworkerai/harness-sdk/adapters/intel-ima";

const adapter = await attachIntelIma(harness, {
  provider: "local-inference", api: "chat-completions",
  baseUrl: process.env.IMA_BASE_URL!, apiKey: process.env.IMA_API_KEY!,
  port: 18875, route: { mode: "pin", target: "my-local-model" },
});
const agent = harness.createAgent({
  name: "assistant", instructions: "Help the user.", tools: [],
  defaultModel: { provider: "local-inference", name: "my-local-model", notifyOnSessionState: true },
});
const session = await agent.newSession();
await session.run("Hello");
await session.close();
// Keep the app and adapter alive for background observer delivery.
// At application shutdown:
await harness.shutdown();
await adapter.close();
```

OpenAI Chat Completions and Anthropic Messages are supported inbound protocols.
For ordinary inference choose auto, prefer or pin. Cloud egress defaults off and
must be enforced at the service before dispatch. Required lifecycle receipts and
status-only requests apply to opted-in individual sessions using a pinned local
model on a stable backend. This adapter treats those sessions as independent main
agents. Team topology and distributed scheduling need separate qualification.

The adapter maps active tool execution to `waiting_tool`, pending human input to
`waiting_confirm`, and explicit close to `done`. Starts ride on inference.
Status calls use no model tokens and are advisory. The adapter skips obsolete
notifications by journal sequence. An already in-flight request needs server-side
ordering for a strict delivery guarantee. The adapter makes a best-effort cancel
request for a disconnected inference attempt. Provider session state is kept in
process; a provider should expire idle caches if an app disappears.

Run the synthetic wire tests against a built `sdk-test-host` by setting
`OW_TS_TEST_HOST` and running `node --test test/provider-lifecycle-live.test.mjs`
from the SDK after `npm run build`. Set `OW_MOCK_IMA_URL` to a private test
fixture URL to use a separate host. The fixture uses synthetic data and should
only be exposed on loopback or through an authenticated tunnel.
