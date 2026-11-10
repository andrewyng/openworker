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
