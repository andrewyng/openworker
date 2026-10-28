# Local installer and guard qualification plan

28 October 2026. Scope: local Docker/kind and simulated cloud APIs only. No AWS
provisioning, artifact upload or identity changes.

## Deliverables and order

1. Native `ows deploy local` plan/apply/status/destroy commands. Bind explicit
   kubeconfig/context and cluster identity, exact chart/values hashes, namespace
   ownership and remote release receipts. Two OpenShell modes: install pinned
   upstream chart in a fresh owned namespace, or use an existing gateway without
   lifecycle ownership. Allow OpenShell-only preparation before service fixtures.
2. Durable intent/result journal and OS-backed exclusive lock. Record intent before
   each mutation. After interruption, inspect the original remote identity. An
   absent object after an uncertain mutation requires explicit no-effect confirmation;
   never blindly reissue a Helm installation. Cleanup checks ownership and stable
   UIDs, and refuses active sessions/environments or live owned sandboxes.
3. Build an immutable expiry-guard package with current OWS environment contract.
   Use injected providers for local failure tests; no AWS calls from the package
   tests. Verify fixed expiry, ownership, bounded object/volume cleanup and stable
   deletion identity. A guard hash is not a live AWS qualification.
4. Local end-to-end qualification: pinned OpenShell installation, PostgreSQL fixtures,
   OpenWorker installation, authenticated real sandbox/tool round trip, process
   restart/resume and cleanup. Repeat using the existing-gateway mode and prove it
   remains untouched. Preserve baseline namespaces/sandboxes.
5. Update guides/evidence/ledger, run targeted checks and commit. Cloud apply remains
   disabled; later AWS testing is needed for IAM, EBS, VPC and actual Lambda cleanup.

## Installation boundary

The native installer owns namespaces/releases it creates. Existing gateways are
external dependencies; it never upgrades/uninstalls them. The local profile expects
Agent Sandbox CRDs/controller, an enforcing CNI and default storage provisioner to
already exist and checks them. It does not replace cluster-wide infrastructure in
a shared test cluster. Fresh cloud clusters will need an explicit platform phase.

OpenWorker chart inputs reference existing Secrets. Secret values are not placed in
the journal. Local PostgreSQL/certificate generation belongs to qualification
fixtures, not the production storage adapter. The first local cleanup profile checks
its dedicated fixture database and freezes API intake before namespace deletion.
Keep this local profile distinct from future managed-database teardown.

## Progress

- Existing local Kubernetes/OpenShell flow, cached Linux images and namespace
  boundaries inspected. About 202 GiB free; reuse caches and avoid a new cluster.
- Native local installer, deterministic guard packager, private fixture preparation
  and real Kubernetes acceptance helpers implemented. No new Rust dependencies;
  Node/Python remain test helpers, not native CLI runtime requirements.
- 32 Rust CLI tests pass. Six local-installer tests exercise lost replies before
  and after remote effect, explicit no-effect recovery, changed cluster/input/
  namespace identities, active work, OS lock, read-only observation, controller
  unavailability and chart inventory overflow. Nine injected-provider guard tests
  pass, including uncertain deletion/control-write outcomes and malformed inventory.
- Installed the pinned authenticated OpenShell 0.1.3 chart in an owned namespace;
  installed OpenWorker and qualified real sandbox write/shell-read through scoped
  service credentials. A fresh installer process preserved Helm release receipts
  and teardown refused active work. Closed the session, deleted its environment and
  removed owned releases/namespaces/PVCs plus gateway cluster RBAC.
- Repeated against the preexisting gateway. Its release revision/receipt and all
  three baseline sandbox UIDs remain unchanged; all baseline namespace UIDs match.
  Both new service test namespaces are gone. Existing gateways remain untouched.
- Real Calico probe passed baseline allow, egress deny/allow, ingress deny/allow.
  Its temporary pods/policies were removed. Reused the existing kind cluster/images;
  free disk remains about 202 GiB.
- Guard ZIP repeats byte-for-byte; Python zipfile independently verified CRC, source
  bytes and manifest SHA. All three newly generated CloudFormation templates pass
  cfn-lint 1.58.0 (only intentional W3010 availability-zone warning suppressed).
- [Guide](OWS-LOCAL-INSTALLATION.md) and [sanitized evidence](evidence/local-installer-20261028.json)
  written. Local D4/D5 subset complete; cloud operation reconciliation, fresh-cluster
  platform bootstrap, upload/secret handoff, private management API connectivity,
  and live cloud guard/IAM qualification remain. No AWS mutation/upload occurred;
  live cloud apply/destroy remain disabled.
