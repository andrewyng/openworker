# NVIDIA OpenShell integration

OpenWorker manages the agent. OpenShell isolates the tools it executes. Together,
they let an application keep durable sessions and policy decisions in the harness
while placing file, process and network activity inside a sandbox.

```text
Application → OpenWorker Harness
                 │  Models, sessions, approvals, guardrails
                 │
                 └─ OpenShell gateway → Sandbox
                                          └─ ToolRunner → Tool actions
```

## Where each component runs

In the current service architecture, the harness and agent loop run outside the
tool sandbox in the trusted service domain. The harness provisions or attaches an
OpenShell environment and dispatches sandboxed work to its ToolRunner.
Application callbacks and explicitly host-placed integrations remain outside that
sandbox. Their placement must be intentional; selecting OpenShell does not magically
isolate arbitrary code running in your application process.

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
[local installer](../OWS-LOCAL-INSTALLATION.md),
[OpenShell Git access](../OPENSHELL-GIT-ACCESS.md) and
[isolated coding teams](../ISOLATED-CODING-TEAMS.md).

OpenShell is the execution boundary. [Agent alignment](alignment.md),
[guardrails](../GUARDRAILS-QUICKSTART.md) and [compliance](compliance.md) provide
additional decisions and evidence around that boundary.
