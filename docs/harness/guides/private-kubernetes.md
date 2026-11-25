# Private Kubernetes cluster

Run the OpenWorker service in a Kubernetes cluster you operate—on premises or in
your own cloud network. Your application connects to the service API; workers run
the agent loop, and NVIDIA OpenShell hosts sandboxed tool execution.

```text
Your application → OpenWorker API → Agent workers
                         │                │
                    PostgreSQL      OpenShell gateway
                                          │
                                    Tool sandboxes
```

## What you provide

- An explicit kubeconfig/context and an installer with the reviewed cluster and
  namespace permissions. Installing sandbox platform components needs more than
  namespace-only access.
- PostgreSQL, persistent storage, reachable image registries and the required
  network/controller components for the selected OpenShell version.
- Service/gateway TLS and scoped credentials delivered through Kubernetes Secrets
  or your secret-management system. Agents do not receive cluster-administrator keys.
- An authenticated route to the OpenWorker API. The Kubernetes management API can
  remain private; the application does not need direct Kubernetes access.

## Installation flow

1. Validate the cluster, storage and network prerequisites.
2. Choose an existing OpenShell gateway or an installer-owned, pinned gateway.
3. Review the OpenWorker Helm values, images, database and secret references.
4. Install the service, establish its first administrator and register RuntimeProfiles.
5. Submit a test agent, inspect its progress and qualify recovery and cleanup.

The `owcluster` installer plans and checks the OpenShell/OpenWorker installation together.
An existing gateway keeps its original owner; selecting it does not authorize the
installer to delete or reconfigure unrelated resources.

The local installer has fixture namespace/context constraints. Use the
[service Helm chart](../../../deploy/service/chart/README.md) for a cluster you
operate; do not treat a kind-only test command as a generic production installer.

Continue with [OpenShell integration](openshell.md) and
[deployment options](deployment.md).
