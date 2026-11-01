# Sandbox Git through OpenShell: local qualification

This fixture runs **real Git smart HTTP over verified TLS through OpenShell**.
The upstream accepts a synthetic secret only; an opaque placeholder reaching it
unchanged fails authentication. Python is used only for this test remote and its
orchestrator. The shipped harness and credential helper remain native Rust.

The [test catalog](../../../../../docs/harness/CODING-AGENT-PRESETS-TESTING.md)
explains the layers. The [journal](../../../../../docs/harness/CODING-AGENT-PRESETS-PLAN.md)
records actual native and public service/SDK runs.
The [service guide](../../../../../docs/harness/OPENSHELL-GIT-ACCESS.md) explains
operator grants, RuntimeProfiles and SDK setup.

## What is exercised

```text
Host qualification process (trusted)
  | creates original sandbox with pinned image + provider/profile
  | opens private ToolRunner connection
  v
Sandbox: Rust ToolRunner -> shell -> git -> Rust credential helper
  |                         opaque OpenShell reference only
  | TLS, endpoint/method policy, Git executable ancestry
  v
OpenShell supervisor/proxy (trusted, separate pod)
  | validates binding, rewrites Basic auth with synthetic upstream token
  | verified upstream TLS
  v
Local Git HTTPS server -> git-http-backend -> disposable bare repository
```

The fixture checks clone, fetch, denied unconfigured repository, read-only versus
explicitly enabled push, no persistent Git helper/key, expiry acknowledgment,
restored validity on a new tool connection using the same process manager,
provider detach, refusal to reattach and original-resource cleanup after detach.
The remote also enforces an `agents/*` branch prefix. **That branch restriction is
remote enforcement, not an OpenShell claim:** HTTP endpoint rules do not parse
branch names inside Git packs. Prefer the trusted publisher for branch constraints.

## Prerequisites and fixture isolation

Use a disposable local kind cluster, a named test namespace and **separate**
OpenShell 0.1.3 gateway, plus Docker, Helm, the matching OpenShell CLI, Git,
OpenSSL and Python 3. Always select an explicit kubeconfig/context. No model,
cloud account or real GitHub credential is required. Do not modify a shared
gateway's CA trust or use `--gateway-insecure`, `GIT_SSL_NO_VERIFY`, raw-token
fallback, or an existing user repository.

Prepare these inputs in a private directory (mode 0700, files 0600):

1. A short-lived fixture CA, server certificate/key for
   `git.<test-namespace>.svc.cluster.local`, and random synthetic token. Mount
   **only** the server certificate/key and token in the Git remote pod at
   `/fixture-secrets/{server.crt,server.key,token}`. Run `server.py` there on 8443,
   with Git, `git-http-backend`, Python and a writable `/tmp`. Give it a Service
   named `git`, bounded resources, and no host mount.
2. Build the Linux runtime and use this directory's `Dockerfile` with an existing
   runner base (`--build-arg RUNNER_IMAGE=<pinned-base>`). The context contains
   `openworker`, `server.py` and **public** `ca.crt` only. Load it into kind and
   record its actual OCI digest; use that digest in the native probe.
3. The agent and **separate OpenShell supervisor** must both trust the public
   fixture CA. For the test gateway only, overlay the public CA onto the pinned
   supervisor's existing `/etc/ssl/certs/ca-certificates.crt` bundle. Keep its
   original certificates and binary. Load that supervisor image into kind and
   select it in the test gateway's Helm values. Do not disable TLS inspection.
4. Install the pinned 0.1.3 chart with authentication enabled, telemetry disabled,
   a 256 MiB default workspace and bounded gateway resources. Obtain its mTLS
   client files through that test installation. Port-forward its management
   endpoint to localhost. Keep the CLI's XDG configuration separate from other
   gateways. In 0.1.3 its mTLS path is
   `openshell/gateways/<sanitized-whole-endpoint-URL>/mtls`.

Create two fixture-only provider profiles, `owgit-c6-read` and `owgit-c6-write`,
and corresponding providers. Use **the same exact host, port and Service IP**
throughout the test. Provider profiles use this policy shape:

```json
{
  "id": "owgit-c6-read",
  "display_name": "Local Git read",
  "category": "source_control",
  "credentials": [{
    "name": "api_token", "env_vars": ["GITHUB_TOKEN", "GH_TOKEN"],
    "required": true, "auth_style": "bearer", "header_name": "authorization"
  }],
  "discovery": {"credentials": ["api_token"]},
  "endpoints": [{
    "host": "git.<test-namespace>.svc.cluster.local", "port": 8443,
    "protocol": "rest", "enforcement": "enforce", "allowed_ips": ["<Service-IP>"],
    "rules": [
      {"allow": {"method": "GET", "path": "/org/project.git/**"}},
      {"allow": {"method": "HEAD", "path": "/org/project.git/**"}},
      {"allow": {"method": "OPTIONS", "path": "/org/project.git/**"}},
      {"allow": {"method": "POST", "path": "/org/project.git/git-upload-pack"}}
    ]
  }],
  "binaries": ["/usr/bin/git"]
}
```

For the write profile, change the ID/name and add **only** POST
`/org/project.git/git-receive-pack`. Omit `tls` to use automatic inspection in
the pinned 0.1.3 importer; it rejects the deprecated literal `terminate`. Import
profiles in the test gateway's platform scope, then create their provider
instances in workspace `default`. Pass the token using the bare
`--credential GITHUB_TOKEN` option and a private subprocess environment, never
a `KEY=value` command argument. Don't print provider credential responses.

## Run the native probe

Build from the repository root:

```sh
CARGO_INCREMENTAL=0 cargo build --locked --manifest-path harness/Cargo.toml \
  -p openworker-runtime --example openshell_git_probe
```

Make a private JSON config with a **fresh** state directory for every independent
attempt. The native probe persists the original intent/provider pin/binding before
subsequent operations; a failed run is inspected, never replaced implicitly.

```json
{
  "endpoint": "https://localhost:<forwarded-port>",
  "certificates": "<private-directory>/gateway-tls",
  "tls_server_name": "localhost",
  "image": "docker.io/library/openworker-runner@sha256:<actual-image-digest>",
  "host": "git.<test-namespace>.svc.cluster.local:8443",
  "provider": "owgit-c6-read",
  "publication": false,
  "state": "<private-directory>/read-attempt-1",
  "barrier": false,
  "retain_failed": true
}
```

The certificate directory holds `ca.crt`, `tls.crt` and `tls.key`. Run the read
test, inspect `result.json`, then check the upstream `/__status`: read succeeds,
push fails and the receive-pack counter remains zero.

```sh
harness/target/debug/examples/openshell_git_probe <private-read-config.json>
```

For a second config, select `owgit-c6-write`, `publication: true`, `barrier: true`
and another fresh state directory. Run:

```sh
python3 -B harness/runtime/tests/fixtures/openshell-git/control.py \
  --cli <openshell-cli> --xdg-config <private-cli-config-directory> \
  --probe harness/target/debug/examples/openshell_git_probe \
  --config <private-write-config.json>
```

The control script changes only the synthetic provider: expire, restore validity,
detach. **A `withheld` control-plane receipt can contain the OLD observed revision.**
It waits by reading the exact original receipt until the supervisor acknowledges
the desired environment/config/attachment before issuing the expiry check. It
doesn't repeat the mutation or test a replacement resource. This qualifies
expiry/restored-validity propagation, not an external OAuth refresh implementation.

## Inspect and clean up

`result.json` contains boolean assertions and cleanup confirmation, not credentials
or Git output. For failed attempts keep `intent.json`, `provider.json`, `binding.json`
and the stage receipts. A lost reply is unknown; inspect those original identities
before deciding any further action. Private failure diagnostics may contain opaque
references; redact before sharing. Never collect headers or packet traces with keys.

After inspection, explicit cleanup of an original saved binding is available:

```sh
harness/target/debug/examples/openshell_git_probe <original-config.json> --cleanup
```

It requires both saved intent and binding and cannot create a replacement. Cleanup
checks original UID, environment ownership, workspace and image even if execution
access was revoked. After all sandbox deletions are confirmed, remove only this
fixture's provider instances/profiles, Helm release, namespace and port-forward.
Retain sanitized evidence; remove private fixture material when no longer needed.

## Fast checks

```sh
python3 -B -m unittest discover \
  -s harness/runtime/tests/fixtures/openshell-git -p test_control.py
cargo test --locked --manifest-path harness/Cargo.toml \
  -p openworker-runtime --lib environment_openshell
cargo test --locked --manifest-path harness/Cargo.toml \
  -p openworker-runtime --lib coding::openshell_git
```

On disposable Linux, also run `--test openshell_git -- --include-ignored` with
`OW_TEST_DISPOSABLE_LINUX=true`. It launches real Git/helper processes in direct
and sandbox-owned-manager modes, checks default secret stripping and explicit
placeholder-only opt-in. Never run that retained-manager test on a shared host.


## Run the service/SDK qualification

The ignored `openworker-server-reference` integration test `service_v2` uses the
same isolated fixture and real Linux runner. Supply the existing `OW_PG_TEST_URL`
and `OW_PROBE_{ENDPOINT,TLS_NAME,CERTS,RUNNER,IMAGE}` inputs, plus
`OW_GIT_PROVIDER`, `OW_GIT_WRITE_PROVIDER` and `OW_GIT_HOST`. The two provider
names must refer to the reviewed read and write profiles above. The test pins
the original non-secret provider/profile identity and installs named service grants.

```sh
CARGO_INCREMENTAL=0 cargo test --locked --manifest-path harness/Cargo.toml \
  -p openworker-server-reference --features openshell-test \
  --test service_v2 -- --ignored --nocapture
```

Leave `OW_GIT_ONLY` unset for the combined regression. `OW_GIT_ONLY=true` selects
focused Git/SDK and existing TypeScript service checks, and requires the Git
fixture inputs. Neither result should be described as the other. This exercises
same-sandbox credential sharing, independent same-grant/credential-free children,
read-only push denial, separate authorized publication, cross-grant rejection,
worker restart and TypeScript resume. The remote hook restricts test branches;
this does not imply OpenShell parses branch names.
