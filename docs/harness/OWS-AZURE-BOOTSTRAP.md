# Azure setup identity and preflight

This milestone exports a customer-reviewed setup grant and checks it read-only.
It does **not** deploy AKS or prove the principal can create infrastructure. No
Azure account or budget has been selected or changed by the implementation work.
The [implementation ledger](OWS-AZURE-IMPLEMENTATION.md) tracks the later stages.

## 1. Prepare the target and principal

The customer supplies:

- An Azure tenant/subscription in the public Azure cloud and an approved region.
- An existing, dedicated resource group. It stays customer-owned; `ows` does not
  create, adopt or assume it may delete the group in this milestone.
- An existing Entra **object ID** for the setup user/service principal, plus its
  type. An application/client ID is a different identifier.
- A bootstrap administrator with scope read access, custom-role definition write
  authority on the assignable scope, and assignment write authority at the RG.
- Trusted `ows`, Azure CLI and Bash. The operator network path to a private AKS
  management endpoint and cluster-level OpenShell installation rights are later
  infrastructure prerequisites.

An interactive setup user uses its existing Azure CLI login. A service principal
needs the customer's approved federation/login flow; identity-enabled Azure
compute can use managed-identity login. Granting a role does not create a login or
let an arbitrary user impersonate a managed identity. No client secret, access
token, Entra application or managed identity is generated/exported here.

## 2. Export an offline review bundle

The UUIDs below are placeholders, not a deployment target or authorization.

```sh
ows deploy azure plan \
  --tenant 11111111-2222-4333-8444-555555555555 \
  --subscription 66666666-7777-4888-8999-aaaaaaaaaaaa \
  --location westus2 --name my-service --budget 100 --ttl-hours 24 \
  --out ./azure-plan

ows deploy azure bootstrap --plan ./azure-plan \
  --resource-group customer-ow-service \
  --setup-object-id bbbbbbbb-cccc-4ddd-8eee-ffffffffffff \
  --setup-principal-type service-principal \
  --out ./azure-bootstrap

ows deploy inspect --plan ./azure-bootstrap
ows deploy validate --plan ./azure-bootstrap
```

Use a fresh output directory. Export, inspect and validate never invoke Azure CLI
or load service authentication. The bundle contains the exact custom-role JSON,
assignment JSON, stable IDs, review text and a separate `azure-bootstrap.sh` script.
The manifest checks integrity, not the identity of a signer.

The role manages ARM deployment metadata and scope/RBAC reads inside the exact RG.
It has no AKS/VM/database/storage creation actions, wildcard rights, role-assignment
writes, data actions, credential export or self-escalation authority. Deleting an
ARM deployment record does not delete its resources. Existing broader/inherited
grants are not removed by adding this narrow role.

## 3. Customer explicitly applies reviewed grants

Review `AZURE-BOOTSTRAP.md`, `azure-identities.json`, the two payloads and the shell
script. Using the bootstrap administrator's trusted Azure CLI login, the customer
may separately run:

```sh
bash ./azure-bootstrap/azure-bootstrap.sh --apply-identities \
  /private/fresh-azure-bootstrap-journal
```

`ows` never invokes this script. It checks cloud/tenant/subscription/RG, refuses
existing generated role/assignment IDs, and writes private receipts. It does not
switch subscriptions, enable providers, install CLI extensions or create paid
infrastructure. The setup principal is the reviewed recipient, which may differ
from the administrator running the script.

A lost response is an unknown outcome. Inspect the original IDs and journal before
retrying; do not overwrite a role or issue a new mutation blindly. There is no
script-level automatic mutation retry; Azure CLI/SDK transport behavior remains
its own concern. The plan's expiry gates new script steps and does not revoke
already-created grants. Retain the journal for explicit later reconciliation.

## 4. Read-only bootstrap preflight

```sh
ows deploy azure preflight --plan ./azure-bootstrap \
  --out ./azure-preflight --timeout-seconds 20 --overall-timeout-seconds 120
```

This does not call the bootstrap script. It allows only scope/account/cloud reads,
exact role/assignment GETs and provider registration observations. Calls have
bounded output, per-call/overall deadlines and redacted failures. It does not issue
`az login`, export tokens, enable providers, change IAM or install extensions.

`bootstrapReady:true` means those exact grant checks passed. The report always
keeps `applyReady:false`, `effectivePermissionsVerified:false` and infrastructure,
workload identity, cost/quota and cleanup readiness false. Provider registration
checks are optional observations for the later infrastructure milestone; no
registration is performed. Use a new report directory after fixing a blocker.

Still required: resolved Bicep/ARM resources and executor permissions, effective
IAM/deny/Policy/lock checks, a verified cost/quotas/budget plan, independent expiry
cleanup, workload federation/data grants, private management access, shared
OpenShell/service Helm installation and a separately authorized live trial.

## Test coverage

`harness/service-cli/tests/azure.rs` runs the real native CLI against an isolated
fake `az`. Tests cover offline integrity, validation before output, exact scopes,
principals/actions, mismatches, redaction, deadlines/output limits, no writes from
preflight, explicit bootstrap confirmation, collision refusal, private journals and
no script retry after a failed mutation. No real Azure CLI/account/network is used.

## References

- [Custom roles and assignable scopes](https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles)
- [Assign roles with object IDs](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-cli)
- [AKS workload identity](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview)

## Next stages

The identity-only guarantee above is unchanged. Resolved graph, lifecycle,
independent cleanup and AKS/shared-installer commands now exist behind explicit
experimental confirmation. See [Azure service reference](OWS-AZURE-SERVICE.md)
and [implementation ledger](OWS-AZURE-IMPLEMENTATION.md) for exact locally tested
behavior and the remaining live Azure qualification gates. Bootstrap preflight
still never means effective-IAM, infrastructure or production readiness.
