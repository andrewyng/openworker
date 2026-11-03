# Build your product. OpenWorker handles the agents.

OpenWorker is a Rust agent runtime with Rust and TypeScript SDKs. Add an agent to
a desktop or server application while keeping your own interface, tools and product
workflow. The harness manages model calls, tool execution, streaming, approvals
and durable sessions.

```text
       Desktop app                 Web application
       Rust / TypeScript           Your API + browser UI
              │                          │
              └────────────┬─────────────┘
                    OpenWorker SDK
                           │
                  OpenWorker Harness
           Models · Tools · Sessions · Teams
          Guardrails · Approvals · Observability
                           │
             Local execution or OpenShell
```

- [Install the SDK](installation.md) from Cargo or npm.
- [Build a desktop app](desktop.md) for Windows or Mac.
- [Build a server app](server.md) in a container or connect to a service cluster.
- [Choose models and credentials](providers.md), including a local model server.

Start with one agent. Add teams, sandboxing and service deployment when your
application needs them. See the [release contract](../SDK-RELEASE-CONTRACT.md)
for the beta compatibility and platform qualification policy.
