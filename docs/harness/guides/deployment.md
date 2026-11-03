# Deployment options

Start with the simplest placement that fits your product.

## Choose a platform

| Platform | Reference | Qualification boundary |
|---|---|---|
| [Private Kubernetes cluster](private-kubernetes.md) | Customer-managed cluster, PostgreSQL and OpenShell | Local Kubernetes/OpenShell qualified; validate your distribution and policies |
| [AWS](aws.md) | EKS, PostgreSQL, S3 and OpenShell | Recorded live trials; fresh current-release installation remains a gate |
| [GCP](gcp.md) | GKE Standard, Cloud SQL, Cloud Storage and OpenShell | Implementation locally qualified; live workload trial outstanding |
| [Azure](azure.md) | AKS, PostgreSQL, Blob and OpenShell | Implementation locally qualified; live deployment trial outstanding |

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

Start with [local installation](../OWS-LOCAL-INSTALLATION.md), then read the
[deployment specification](../OWS-DEPLOYMENT-SPEC.md) and
[architecture scenarios](../SERVICE-ARCHITECTURE.html). Cloud credentials, budgets
and cluster identity are operator-owned. Consult each cloud's qualification ledger
before treating its deployment path as supported.

Before upgrading, follow the [release contract](../SDK-RELEASE-CONTRACT.md) and
[current schema upgrade procedure](../GUARDRAILS-QUICKSTART.md#upgrade-an-existing-installation).
