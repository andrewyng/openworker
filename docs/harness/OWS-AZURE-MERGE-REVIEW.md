# Azure offline branch merge review

Reviewed Azure `69e805da` against canonical `fd19e1bb`, with common ancestor
`f13de9b9`. No cloud account/API/IAM operation, provisioning, push or merge was
performed. This review does not qualify live Azure or the combined merged tree.

## Merge compatibility

At these revisions the changed-file sets are disjoint (30 Azure paths and 57
canonical paths). Azure adds the deployment
CLI/provider, artifact adapter and shared installer/chart wiring; canonical adds
team types, workspaces, briefing and approval provenance. No textual conflict is
apparent. The SDK service methods consumed by the existing CLI remain compatible
on static inspection; canonical's SDK-host return-type change is internal to the
SDK adapter. Its new private workspace CLI channel does not replace the existing
ToolRunner entry points used by the deployment.

The combined tree must still compile and run the parent's SDK/team matrix after
an authorized merge. The offline Azure results below were obtained on the Azure
feature branch, not a synthetic merged tree. Do not reuse another worktree's
active Cargo target while qualifying different source revisions.

## Concrete findings and fixes

1. **Self-stop uncertainty could disappear from status.** The old implementation
   persisted a stop request before DELETE, but later ticks neither observed its
   result nor exposed it in status. A rejected or lost DELETE could therefore
   leave recurring executions while output looked completed. Stop is now durable
   `intent → accepted/unknown → absent`. Later ticks and explicit lifecycle
   reconciliation only read the exact original job; they never repeat DELETE.
   Workload completion and controller absence are separate status fields. Unknown
   or crash-intent states require attention, and stop-observation ticks do not
   refresh the healthy heartbeat. A 202 acknowledgement remains pending until a
   read proves absence. A controller already deleting need not appear healthy
   for ownership-only stop observation.
2. **The scheduled execution learned its own first receipt.** Previously initialize
   wrote control without pinning the controller, and the first heartbeat adopted
   its creation timestamp. Explicit initialization now obtains authenticated ARM
   metadata and pins job creation time plus the assigned managed identity's
   principal/client IDs before the create-only Blob write. Tick refuses missing
   receipts and compares its requested identity with that pin. Job arguments must
   match the provider's actual client ID. Same-name replacements before the first
   heartbeat, altered identity/client configuration and non-ready jobs fail
   closed. No new IAM permission is needed: the existing exact-job read returns
   the identity metadata.

The bootstrap administrator remains a trusted party and must exclusively reserve
these generated IDs. Explicit initialize selects the reviewed current bootstrap
job; it is not cryptographic attestation of an administrator's actions. The
provider GET, nonce/configuration checks and immutable receipt are bound before
any paid workload can pass the independent-heartbeat gate.

## IAM and operational limits before live qualification

- Ordinary executor grants have no wildcard actions, RBAC writes, account-key/SAS
  export or subscription Owner. Cleanup is limited to the explicit resource types
  in the **dedicated workload RG**, with software enforcing exact IDs/receipts.
  The RG-level role is not an IAM per-resource-instance constraint; that group
  must not host unrelated resources. Administrator-only grants/federation remain
  separately reviewed exports, not silently applied by `ows`.
- Bootstrap preflight verifies exported grant records. It does not prove the
  token caller's object ID, all effective permissions/denies or Azure Policy.
  Native CLI token acquisition pins tenant/subscription; using the intended
  existing setup identity remains an operator responsibility.
- Unknown writes are never automatically replayed, including 404 after a lost
  create. An uncertain original request can stay parked for operator inspection;
  there is no general automatic repair/reset command. This is deliberate failure
  behavior, not a successful expiry/teardown claim.
- ARM create-or-update and resource DELETE do not provide a universal atomic
  ownership fence. Generated IDs require exclusive provisioning/teardown
  coordination. Receipt comparison detects observed replacements, not a race
  against a privileged competing writer between GET and mutation.
- Workload expiry does not remove all control data or every cost. The job stops
  itself once; control Blob/account, managed identity, consumption environment and
  reviewed grants remain for audit and explicit customer removal.
- A separately authorized live trial must prove provider API shapes and creation
  receipts, actual IAM/Entra membership, identity endpoint behavior, job scheduling,
  region/version/SKU/quota/prices, private routes, OIDC/admission, Blob data roles,
  CSI/network enforcement, OpenShell runtime and complete resource cleanup. AKS
  also exposes a provider `resourceUID` suitable for reuse detection; prefer it if
  actual creation metadata is not consistently returned, rather than weakening
  ownership checks or falling back to tags.
- This is unreleased development control state. Regenerate previous offline state
  fixtures for the revised receipt/stop format; no deployed-data migration is
  claimed. Build a matching pinned native cleanup image before a live trial.

## Exact offline checks

Use a dedicated task-owned target, one job, debug info disabled and incremental
compilation disabled. The review run capped that target at 6 GiB and removed only
that marked target afterwards. No primary-worktree target was used.

```sh
export CARGO_TARGET_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ow-azure-review.XXXXXX")"
export CARGO_BUILD_JOBS=1 CARGO_INCREMENTAL=0
export CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0

cargo test --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-service-cli --bin ows \
  --test azure --test deployment --test gcp --test local_installer

cargo test --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-service-cli --bin ows deployment::azure::recovery::review_tests

cargo clippy --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-service-cli --bin ows
```

The A4 artifact/chart/server checks remain documented in the main ledger and
sanitized evidence. This follow-up changes only Azure CLI state/receipt handling
and documentation. It adds six focused regression cases covering installer pinning,
first-tick replacement, wrong client/missing receipt, lost initialization response,
stop rejection/response loss/restart, 202 pending/absence and crash-before-dispatch.
No new model call, container, Kubernetes cluster or paid resource is required.

Primary schema references:
[Container Apps jobs](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/app/resource-manager/Microsoft.App/ContainerApps/stable/2024-03-01/Jobs.json),
[managed identity response](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/common-types/resource-management/v3/managedidentity.json),
[AKS reuse identity](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/containerservice/resource-manager/Microsoft.ContainerService/aks/stable/2024-10-01/managedClusters.json).


After parent review and an authorized merge, qualify the combined tree in that
separate target as well:

```sh
cargo check --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-service-cli -p openworker-server-reference
cargo test --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-server-reference --test azure_artifact_config
cargo test --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-artifacts --features azure,s3
node --test deploy/service/chart.test.mjs
```

Rerun the parent's canonical SDK/team matrix after combining the source revisions;
this feature-branch review does not substitute for that qualification.
