# GCP installation wiring and local agent acceptance

October 30, 2026. Working branch: `feat/ows-gcp-installation`, from `02ea4790`.
Integration target: `openworker-harness-sdk`. Authorized cumulative GCP budget:
**US$100**. No increase, no blanket IAM grants, no public management endpoint.

## Implementation sequence

1. **Installation contract and read-only preflight.** Bind the private installation
   journal to the compiled workload graph, original lifecycle request identity and
   pinned cleanup image. Check exact project, enabled APIs, selected zone/version,
   transient-node CPU/disk headroom, control bucket privacy, registry image digest
   and effective setup permissions. Distinguish control-install readiness from
   workload-create readiness. Unknown cost, unavailable private access and unproved
   executor/node/cleanup authority remain explicit blockers, not successful checks.
2. **Resumable artifact installation.** Persist per-object intent before uploading
   the deterministic source ZIP, immutable control and initial checkpoint. Use
   create-only generation preconditions, verify exact returned/read-back bytes and
   generations, and reconcile a lost response by reading rather than replaying.
   Preserve an advanced checkpoint. Never replace a conflicting object, adopt an
   unrelated job, rewrite an existing checkpoint or delete recovery evidence.
3. **Cleanup installation handoff.** Produce the actual generation-bound cleanup
   package from installation receipts. Verify the existing job/scheduler and exact
   service-account/image/arguments/UID bindings before claiming readiness. Record
   remaining reviewed foundation/IAM/job-creation steps if their prerequisites are
   absent. Do not run Terraform apply, enable APIs, widen IAM or launch GKE merely
   to turn a local qualification green.
4. **Qualification and integration.** Exercise nominal, denied, expired, concurrent,
   corrupt, lost-reply, drift and restart paths with local HTTP/storage fixtures.
   Run the full CLI regression suite, pinned Terraform schema validation where
   graphs change, formatting and documentation checks. Merge verified source and
   evidence into the integration branch. Read-only cloud observations must use the
   existing exact project/operator and must not disclose keys or tokens.
5. **Long-running local acceptance.** On the retained local Kubernetes/OpenShell
   installation, run a real model-driven web-app build with multiple tool steps,
   verification and follow-up edits. Preserve workspace/run IDs, observe progress
   through the management UI and telemetry, inspect the built app and exercise
   reconnect/recovery where safe. Note missing coding/process/network/preview
   capabilities and fix reproducible harness defects. Keep provider keys private.

## Qualification boundaries

A generated grant request is not an applied/effective IAM policy. A successful
upload is not a healthy expiry guard. A healthy guard is not GKE/OpenShell kernel,
NetworkPolicy, private installer-route, SQL identity or orphan-free cleanup proof.
Production workload creation stays closed until those independent gates pass.
Prices and cumulative prior spend need fresh evidence before paid installation.
The original four-hour trial plan cannot be extended silently to reuse an expired
approval. Local tests use synthetic identities and no paid resources.

## Progress ledger

- Contract recorded before implementation. The previous milestone passed 95 CLI
  tests and is integrated at `02ea4790`.
- Working tree was clean before this feature. Approximately 147 GiB free disk space;
  reuse existing toolchains/images and avoid unnecessary cloud or image builds.
- Next: implement installation journal/preflight and loss-safe object upload.

### Implementation checkpoint

- Installation journal, scoped preflight, no-replay object protocol, actual-receipt
  package export and job/heartbeat verification implemented.
- Twelve new local object-protocol tests pass. Initial full CLI run: **115 pass**.
  Minor subsequent review changes explicitly bind control Terraform to the setup
  identity and accept an omitted empty permission list as no permissions.
- Read-only GCP project observations completed with no cloud mutations. Missing
  Scheduler/SQL APIs and unqualified billing/private-access/IAM remain blockers.
- Foundation and Cloud Run/Scheduler resource creation are not silently automated:
  they remain a reviewed Terraform step. The command guide states that boundary.
- Next: final regression/schema checks, integration, then local long-agent acceptance.

### Verified milestone

- Final complete CLI regression: **115 passed, 0 failed, 0 ignored**. Includes
  eighteen new native object/HTTP protocol tests and seventeen total GCP CLI
  integration groups. Real API/loopback transport was exercised without cloud
  credentials; invalid inputs are rejected before authentication or effects.
- Both final control Terraform graphs validate with **zero errors/warnings** using
  pinned Terraform 1.5.7 / Google 6.44.0 and the already cached provider. No plan or
  apply. The explicit setup-impersonation field is included in that validation.
- Final Clippy completes with no GCP diagnostics; formatting, whitespace and edited
  documentation links pass. Runtime and both SDK source trees remain unchanged.
- Offline prepare/status/foundation/package commands also pass from the built CLI.
  Native artifact upload/reconciliation is locally qualified, not live-installed.
- No new cloud mutation, IAM grant, API enablement, registry push or paid resource.
  Cumulative budget remains $100; current billing spend is not measured.
- [Sanitized qualification evidence](evidence/gcp-installation-local-20261030.json).
- Next: return this verified milestone to the integration branch, then exercise a
  longer coding agent on local Kubernetes/OpenShell in a separate configuration.
