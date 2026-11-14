# Azure

The Azure reference targets AKS, PostgreSQL and Blob artifact storage, with separate
managed identities for installation, cluster/workload operations and independent
cleanup. OpenShell supplies the sandbox execution layer.

The bounded node choices are `Standard_D4s_v5`, `Standard_D8s_v5` and
`Standard_D4as_v7`. DSv5 is not an SDK requirement: select a supported AMD64 size
with available quota and no applicable subscription restriction. The four-vCPU
choices provide 16 GiB per node; the reference creates two Ubuntu nodes.

When the exported plan includes infrastructure, `owcluster deploy azure preflight`
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
owcluster deploy azure preflight --plan ./azure-infrastructure --out ./azure-preflight \
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

For a private cleanup image, set `guardRegistryResourceId` to an existing ACR in
the control resource group and use its digest-pinned image in `guardImage`. The
cleanup package configures managed-identity pulls and exports an exact `AcrPull`
assignment for review. Registry passwords and workload-owned cleanup images are
not supported.

An optional `managementAccess` object accepts `operatorIpv4` and a pinned
`ubuntuImageVersion` for Canonical's `ubuntu-24_04-lts:server` image. With Dasv7
nodes, this adds one `Standard_D2as_v7` management VM, restricted to SSH from that
single IPv4 address. The VM, NIC, public IP and security group are tracked by the
same expiry cleanup as the workload; its generated OS disk uses delete-on-VM-delete.
The private AKS API remains private. Preflight includes the extra two vCPUs; a
verified SSH tunnel can keep the operator's Azure credentials on their machine.

This deployment path is experimental. Validate live Azure permissions, network,
OpenShell compatibility and independent cleanup in your subscription before use.

Start with the [service CLI](../../../harness/service-cli/README.md) and
[deployment options](deployment.md).

## Plan a deployment from source

The service CLI is a source-built operator tool, separate from the npm SDK. This
experimental flow requires authenticated cloud access, scoped bootstrap permissions,
a prepared setup profile and a release manifest with pinned images. The config
alone is not sufficient for a fresh account.

```sh
cargo build --manifest-path harness/Cargo.toml --locked --release -p openworker-service-cli
cp deploy/service/up/azure.yaml deployment.yaml
# Edit the account/subscription, region, sizing and reviewed setup references.
harness/target/release/ows deploy azure up --config deployment.yaml --plan-only
```

Review the plan and prerequisites before provisioning. To apply that reviewed
configuration, run the same command without `--plan-only`. It requests approval
before cloud changes. Plan estimates and budget values are not hard billing caps.
Keep the deployment state directory for resume and cleanup.

Use the [complete coordinator instructions](../../../deploy/service/up/README.md)
for setup identities, image preparation, private access and connection output.
The forthcoming consolidated YAML and UI/recording controls are being qualified
separately; do not assume an older `ows` binary accepts those options.
