# Tools and MCP

Expose only the tools an agent needs. The agent definition lists registered names,
such as `coding.read_file` or `app.show_preview`.

| Tool implementation | Registration | Execution |
|---|---|---|
| App callback in TypeScript/Rust | `registerTools` / `register_tools` | Application process; private callback channel when managed |
| Native Rust tool crate | Tools SDK registry | Compiled into your chosen host/runner |
| External MCP server | Explicit server registration | Stdio-launched service or configured HTTP endpoint |

Tools declare JSON input schemas and return structured results/errors. Cancellation
and progress are part of the interface. A callback's process placement is distinct
from its permission classification: registering a tool does not grant unrestricted
file, network or screen access.

Use the bundled coding tools for workspace files, search, patches, processes and
Git. Use the [tools SDK](../../../harness/tools-sdk/README.md) for native tools
and the [TypeScript SDK](../../../harness/typescript-sdk/README.md) for app callbacks.

For external services, keep endpoints and credentials in trusted registration
configuration. Guardrails may inspect selected arguments/results, and authorization
still runs before execution. See [Guardrails guide](guardrails.md).
