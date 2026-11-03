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

**Qualification:** recorded AWS trials exercised real EKS/OpenShell execution,
artifact access, recovery and independent expiry cleanup. Those results apply to
the pinned trial configurations. A fresh installation of the current consolidated
SDK release remains a release gate; older trial evidence does not automatically
qualify every new release or customer topology.

See [AWS preflight](../OWS-AWS-PREFLIGHT.md),
[infrastructure export](../OWS-AWS-INFRASTRUCTURE-EXPORT.md),
[lifecycle and cleanup](../OWS-AWS-LIFECYCLE.md) and
[trial evidence](../OWS-AWS-TRIAL-PLAN.md).
