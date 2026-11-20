# NVIDIA OpenShell integration

OpenWorker keeps the session history and approvals in the cluster worker. OpenShell isolates workspace tools and can also host the agent's model loop.

## Where each component runs

```text
sandboxedtools
  Cluster worker         history, approvals, agent loop → model
    └─ OpenShell         ToolRunner → project files

sandboxedloop
  Cluster worker         history, approvals, authorized tool dispatch
    ├─ Private OpenShell sandbox   Rust agent loop → model
    └─ Tool OpenShell sandbox      ToolRunner → project files
```

For `sandboxedloop`, the worker starts the Rust process over the gateway's authenticated exec stream. The process receives its turn context and sends back model output and proposed calls. The worker commits each proposal before acknowledging it. It can use the journal to resume a turn after a disconnect, including a turn waiting for a person to approve a tool call.

Application callbacks and explicitly host-placed integrations run in their configured hosts.

## Profiles, environments and teams

A RuntimeProfile describes the execution provider and permitted resources. An
execution environment is a provisioned instance with a durable binding. Sessions
can share an environment when authorized, or use independent child sandboxes.
Worktrees/directories help coordinate shared work; independent sandboxes need an
explicit source/artifact handoff.

File, network and credential grants remain separate from tool labels and approval
modes. Named Git grants can use OpenShell's credential delivery path without
placing the real GitHub key in the agent's prompt or tool arguments.

## Installation and verification

The installer can use a reviewed existing gateway or install a pinned gateway it
owns. It checks the endpoint, TLS identity, images and runtime readiness before the
service depends on it. Gateway versions are qualification pins, not a promise that
any upstream release is interchangeable.

See [private Kubernetes](private-kubernetes.md),
[deployment options](deployment.md) and [sessions and teams](sessions.md).

OpenShell is the execution boundary. [Agent alignment](alignment.md),
[Guardrails guide](guardrails.md) and [compliance](compliance.md) provide
additional decisions and evidence around that boundary.

## Choose cluster loop placement

A cluster can offer two activated runtime profiles. Both execute workspace tools inside OpenShell. With `sandboxedtools`, the cluster worker calls the model. With `sandboxedloop`, a dedicated OpenShell sandbox runs the model loop and a separate tool environment owns the project files. The cluster worker records the journal, runs configured approval reviews and dispatches approved calls. Embedded desktop sessions continue to use their local harness.

```ts
const agent = await service.agent("builder@1");
const session = await agent.newSession({ runtimeProfile: "sandboxedloop" });
const saved = session.reference();
// After reconnecting to the same service:
const recovered = await service.resume(saved);
```

**Operator setup.** Install a runner image containing the matching `openworker` binary. Attach an endpoint-bound inference provider to the loop role. The provider profile must allow the binary at `/opt/openworker/openworker` to call the intended native model endpoint. Obtain the non-secret provider pin with the service binary:

```sh
openworker-server-reference --config service.json pin-loop-provider spark-inference
```

In the service configuration, set `openshell.loop_inference` with `connection` (the allowed working model connection), `api`, `base_url` and `provider` (the returned pin). The API, controller and workers must use the same installation. The sandbox receives the provider's placeholder, while real inference credentials stay with the trusted provider. HTTPS is the normal model endpoint. On a private deployment the sandbox-specific route also accepts an operator-pinned RFC1918 IPv4 HTTP endpoint. Keep that entire path on a trusted private network.

Register and activate each immutable profile before deploying the release:

```ts
for (const [name, loopPlacement] of [
  ["sandboxedtools", "service"],
  ["sandboxedloop", "sandbox"],
] as const) {
  await service.registerRuntimeProfile({
    id: name + "@1", provider: "openshell", loopPlacement,
    cpu: 1, memoryMB: 512, sharing: "exclusive",
    approvalMode: "ask", allowWorkspaceReads: ["src", "docs"],
  });
  await service.activateRuntimeProfile(name + "@1", { alias: name });
}
```

List the allowed versioned IDs in the release's `runtimeProfiles` and choose its `defaultRuntimeProfile`. Omitted placement on an older profile means `service`. Admission reserves quota for the private loop environment in addition to any new tool environment. Closing the session queues its dedicated loop sandbox for cleanup. The tool environment follows its existing lifecycle. Agent teams and standalone sub-agent releases currently require service-loop placement. Two independent sandboxed-loop sessions can still share an authorized project workspace.

**Approvals.** One sandbox response can propose several calls. Each receives its own decision. An unanswered approval stays in the cluster journal across stream or worker loss. `approvalMode: "bypass"` skips review while retaining hard policy checks. `allowWorkspaceReads` skips review for the first-party file-read tool beneath the named workspace-relative directories. Custom tools, shell commands and writes follow their normal review policy. An attached workspace provider must implement scoped reads to execute a directory grant.
