# Custom applications and tool packages

The normal desktop integration uses the [Rust SDK](../harness-sdk/README.md) or
[TypeScript SDK](../typescript-sdk/README.md). Begin with the
[Desktop app guide](../../docs/harness/guides/desktop.md).

Use this directory when your app needs a custom executable with linked Rust tools,
or when it implements the private process protocol directly.

- [Tools and MCP](../../docs/harness/guides/tools.md):
  `build-sdk.mjs` generates a wrapper crate and executable sharing one registry.
- [Native tool example](../examples/native-tools-runtime/README.md):
  notification, device and optional computer-use crates.
- [Rust SDK](../harness-sdk/README.md) and [TypeScript SDK](../typescript-sdk/README.md): app, agent, session and run APIs.
- [Models and credentials](../../docs/harness/guides/providers.md): access tokens, callbacks,
  proactive rotation and restart handling.
- [Model connections](MODEL-CONNECTIONS.md), [Guardrails guide](../../docs/harness/guides/guardrails.md) and
  [computer-use controls](COMPUTER-USE.md).

The old application-specific Linux partner kit is not distributed here.
Lantern and Luma are not part of this release candidate; reusable tool crates and
direct-stdio regression tests remain as independent examples/tests.
