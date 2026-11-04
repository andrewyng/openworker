# Build your product. OpenWorker handles the agents.

Creating an app that uses AI should be easy. Builders should be able to focus on a
useful, thoughtful product experience while the harness handles agent loops,
permissions and alignment. **OpenWorker Harness SDK** gives you the foundation for
Claude- or Codex-style agent experiences—conversation, tools, streamed activity and
ongoing sessions—inside your own product.

Build a desktop app for your users, a web app deployed to your cloud, or both.
The Rust and TypeScript SDKs let you choose the deployment that fits your product.
Guardrails, sandbox integration, an optional auto-approver, session durability,
agent alignment controls, service scaling and support for multiple LLM providers
are built into the platform, so you can configure these capabilities instead of
implementing them from scratch.

You can configure **NVIDIA OpenShell** to isolate your agents' tool execution.
The harness coordinates model calls, policy and sessions; the sandbox bounds what
its ToolRunner can access. See [OpenShell integration](openshell.md) for that boundary.

## Choose a track

- **[Build a desktop app for your users](desktop.md).** Create Code Atlas: select a
  code directory and stream a guide to its structure and entry points.
- **[Build a web app and deploy to the cloud](server.md).** Create PDF Notes: upload
  a PDF, ask questions and get answers with page references.

Both tracks include a small UI, agent creation and packaging/deployment instructions.
Open the source drawer to explore the complete example files alongside the guide.

```text
       Desktop app                 Web application
       Your interface              Your API + browser UI
              │                          │
              └────────────┬─────────────┘
               OpenWorker Harness SDK
                           │
                  OpenWorker Harness
           Models · Tools · Sessions · Teams
          Guardrails · Approvals · Observability
                           │
           Tool execution: local or OpenShell
```

Start with the example, then choose the [models](providers.md),
[tools](tools.md) and [deployment](deployment.md) your product needs.
The [release contract](../SDK-RELEASE-CONTRACT.md) records registry availability,
compatibility and platform qualification.
