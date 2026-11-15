# Deployment options

Start inside your app, then choose a shared service when you need one.

Desktop packaging and a Node container on Railway use the managed SDK runtime.
The `ows` CLI installs and operates a separate OpenWorker service cluster; it is
not needed to deploy every SDK app.

## Choose a platform

| Platform | Status and starting point |
|---|---|
| [Railway](railway.md) | Application hosting; single-service recipe tested live |
| [Private Kubernetes cluster](private-kubernetes.md) | Bring an existing cluster; local qualification does not cover every remote environment |
| [AWS](aws.md) | Experimental EKS service deployment |
| [GCP](gcp.md) | Experimental; complete live qualification pending |
| [Azure](azure.md) | Experimental AKS service deployment; fresh-install qualification ongoing |

The application-facing service API stays the same. Cloud identity, networking,
storage and lifecycle setup are provider-specific. [NVIDIA OpenShell](openshell.md)
is the sandbox integration across these reference deployments.

## Choose execution placement



| Placement | Use it when |
|---|---|
| Managed local harness | A desktop app or one container owns a workspace |
| Service with nearby execution | Several API nodes need durable sessions and shared worker capacity |
| Service with separate execution | Sandboxes need different hardware, capacity or lifecycle from the agent workers |

SQLite serves local applications. PostgreSQL supports service deployments and can
also be selected through the Rust SDK. Database choice does not remove team/session
capabilities. Bind an execution environment explicitly when agents must share it;
sharing does not resolve conflicting file edits or ports automatically.

OpenShell runs tool execution in a sandbox while the current harness remains in
the trusted service domain. RuntimeProfiles select the provider and allowed
resources. A profile is configuration; a provisioned environment is an instance.

Start with the [local service setup](../../../deploy/service/README.md) and
[service CLI](../../../harness/service-cli/README.md). Cloud credentials, budgets
and cluster identity are operator-owned. Cloud deployment paths are experimental;
validate the configuration in the target environment before production use.

Before upgrading, read [versions and compatibility](releases.md) and the
[storage migration instructions](upgrades.md).
