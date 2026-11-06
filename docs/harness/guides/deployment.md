# Deployment options

Start with the simplest placement that fits your product.

## Choose a platform

| Platform | Reference |
|---|---|
| [Private Kubernetes cluster](private-kubernetes.md) | Your cluster, PostgreSQL and OpenShell |
| [AWS](aws.md) | EKS, PostgreSQL, S3 and OpenShell |
| [GCP](gcp.md) | GKE Standard, Cloud SQL, Cloud Storage and OpenShell |
| [Azure](azure.md) | AKS, PostgreSQL, Blob and OpenShell |

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
[storage migration instructions](guardrails.md#upgrade-an-existing-installation).
