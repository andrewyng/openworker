# Azure

The Azure reference targets AKS, PostgreSQL and Blob artifact storage, with separate
managed identities for installation, cluster/workload operations and independent
cleanup. OpenShell supplies the sandbox execution layer.

The bounded node choices are `Standard_D4s_v5`, `Standard_D8s_v5` and
`Standard_D4as_v7`. DSv5 is not an SDK requirement: select a supported AMD64 size
with available quota and no applicable subscription restriction. The four-vCPU
choices provide 16 GiB per node; the reference creates two Ubuntu nodes.

When the exported plan includes infrastructure, `ows deploy azure preflight`
also checks the selected SKU's architecture, capabilities and regional eligibility,
plus unused regional and VM-family quota for the initial node pool. Zone-only
restrictions remain visible because the reference does not request availability
zones. These checks neither reserve capacity nor qualify AKS/OpenShell execution.
Budget separately for any management VM and upgrade surge; their CPU requirements
are not included in the initial-pool quota check. `applyReady` remains false until
the rest of the deployment is qualified.

Azure's SKU catalog can take over a minute to enumerate. For an infrastructure
plan, allow a bounded longer read without changing the subscription or retrying:

```sh
ows deploy azure preflight --plan ./azure-infrastructure --out ./azure-preflight \
  --timeout-seconds 180 --overall-timeout-seconds 300
```

## Setup flow

1. Select the tenant, subscription, region and dedicated workload/control resource groups.
2. Review the exact-scope Entra/managed-identity grants and existing secret references.
3. Export and validate the ARM infrastructure and independent cleanup configuration.
4. Verify private AKS/database access, storage, images and OpenShell prerequisites.
5. Qualify installation, a real agent workload, recovery and cleanup in the subscription.

The workload and cleanup control have separate lifecycles. The installer does not
assume subscription Owner rights, and the normal executor is not granted permission
to change role assignments. Keep customer-owned/shared resources outside the trial's
cleanup scope.

This deployment path is experimental. Validate live Azure permissions, network,
OpenShell compatibility and independent cleanup in your subscription before use.

Start with the [service CLI](../../../harness/service-cli/README.md) and
[deployment options](deployment.md).
