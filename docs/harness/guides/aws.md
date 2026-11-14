# AWS

The AWS reference places OpenWorker on EKS, uses PostgreSQL for durable service
state and S3 for artifacts, and integrates NVIDIA OpenShell for tool sandboxes.
API and worker capacity can scale separately from sandbox execution capacity.

## Setup flow

1. Select the account, region, workload scope, budget and expiry policy.
2. Review the exported setup identity and workload roles. Installation authority
   is separate from pod access to artifacts; agents do not inherit the installer.
3. Run read-only preflight for identity, quotas, networking and pinned versions.
4. Provision the reviewed infrastructure, then install OpenShell and OpenWorker.
5. Submit an agent and verify streaming, durable state, artifacts and recovery.

Keep the Kubernetes management API private and provide the installer an authorized
network route. Expose the authenticated OpenWorker API to your application. Budget
alerts are not a hard spending cap; the reference's independent cleanup mechanism
is part of the trial lifecycle.

This deployment path is experimental. Validate your account policies, network,
OpenShell version and recovery/cleanup behavior before using it for production.

Use the [AWS deployment commands](../../../deploy/aws/README.md) and
[service CLI](../../../harness/service-cli/README.md) for setup and operation.

## Plan a deployment from source

The service CLI is a source-built operator tool, separate from the npm SDK. This
experimental flow requires authenticated cloud access, scoped bootstrap permissions,
a prepared setup profile and a release manifest with pinned images. The config
alone is not sufficient for a fresh account.

```sh
cargo build --manifest-path harness/Cargo.toml --locked --release -p openworker-service-cli
cp deploy/service/up/aws.yaml deployment.yaml
# Edit the account/subscription, region, sizing and reviewed setup references.
harness/target/release/ows deploy aws up --config deployment.yaml --plan-only
```

Review the plan and prerequisites before provisioning. To apply that reviewed
configuration, run the same command without `--plan-only`. It requests approval
before cloud changes. Plan estimates and budget values are not hard billing caps.
Keep the deployment state directory for resume and cleanup.

Use the [complete coordinator instructions](../../../deploy/service/up/README.md)
for setup identities, image preparation, private access and connection output.
The forthcoming consolidated YAML and UI/recording controls are being qualified
separately; do not assume an older `ows` binary accepts those options.
