# Local server harness qualification

This directory tracks the dedicated `openworker-poc` kind environment. It is an
implementation/qualification profile, not a production deployment package.
Versions are pinned in `versions.env`; `kind.yaml` pins the node image digest.
Use a separate kubeconfig, never the operator's default context.

Prerequisites: Docker Desktop, checksum-verified ARM64 kind/kubectl/Helm/OpenShell
binaries from their official releases. Linux Rust builds use
`rust:1.96-bookworm@sha256:a339861ae23e9abb272cea45dfafde21760d2ce6577a70f8a926153677902663`.
On the initial Mac, binaries/state are in sibling `.harness-tools`/`.harness-local`
directories outside this repository. These contain local secrets and are not
artifacts to commit.

```sh
export KUBECONFIG=/absolute/private/path/to/openworker-poc.kubeconfig
kind create cluster --config deploy/local/kind.yaml --kubeconfig "$KUBECONFIG"
kubectl apply --server-side -f https://raw.githubusercontent.com/projectcalico/calico/v3.33.0/manifests/calico.yaml
kubectl apply --server-side -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v1.0.6/sandbox.yaml
# Verify CNI and controller readiness, then run the network policy probes.
helm upgrade --install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.3 --namespace openshell --create-namespace \
  --values deploy/local/openshell-values.yaml
```

The local gateway uses a developer principal, a private ClusterIP and TLS. It
must not be exposed beyond localhost port-forwarding. Customer deployment needs
its own trusted app access path / OpenShell identity configuration. Disabling
OpenShell's anonymous vendor telemetry is separate from the planned harness OTel
export to the customer's collector.

Qualification requires actual ingress/egress policy deny and allow tests,
Landlock/seccomp enforcement, workspace persistence, runner MCP communication,
and observed disconnect/cancellation behavior. A Ready pod is insufficient.
See `docs/harness/SERVER-HARNESS-JOURNAL.md` for measured results and limits.

For another isolated kind test cluster, pass a separate kubeconfig and explicit
expected context to the network probe:

```sh
KUBECONFIG=/absolute/private/path/to/server.kubeconfig \
OW_PROBE_CONTEXT=kind-ow-server-20261011 python3 deploy/local/probe-network.py
```

The probe refuses non-kind/default context mismatches and an existing probe
namespace. It removes only its own `ow-network-probe` namespace afterward.


## Runner and live MCP probe

Build `openworker-runtime` for Linux ARM64, copy/strip its `openworker` executable
into a temporary build directory, then build using `runner.Dockerfile` and load
with `kind load docker-image --name openworker-poc IMAGE`. Create a sandbox with
`sandbox-policy.yaml` through OpenShell. The example `openshell_probe` takes
`OW_PROBE_ENDPOINT`, `OW_PROBE_CERTS`, `OW_PROBE_SANDBOX` and optional
`OW_PROBE_TLS_NAME` (explicit verified certificate name for port-forwarding).
It initializes MCP, lists tools, writes a public marker and reads it back.
Credentials belong in private local files/Secrets, never in image build context.

`openshell_shared_probe` uses the same variables and two independent MCP streams.
It checks launch-ID rejection, survival of the other stream after disconnect,
late file effects, poisoning after a cancelled request, and kernel/filesystem
boundaries. It intentionally writes uniquely named markers inside the selected
qualification sandbox; never point it at an application workspace.

Measured on 2026-10-11: a delayed shell write **continued after stream drop**.
Treat disconnect/cancellation as potentially ongoing effects, not as sandbox-wide
termination. Idle stdin close returned an exit event in this run. Neither result
proves arbitrary descendants or external effects are stopped. Keep these as
separate attachment-close and tool-outcome observations.

OpenShell 0.1.3 CLI caveat: `gateway add --local` imports package-managed local TLS
credentials when present, even if the named gateway already has a copied bundle.
For this private Kubernetes test, register first and then install that cluster's
client TLS bundle into the task-local gateway directory. Verify its CA fingerprint
against the cluster Secret and keep hostname/certificate validation enabled. Do
not copy or replace the operator's default gateway configuration. The harness
provider should use explicit certificate paths, not CLI auto-discovery/import.

The local gateway currently uses a developer principal; this is an explicit
local qualification setting. The client still verifies TLS and presents its
certificate. Do not reuse this identity mode for a customer-facing gateway.
