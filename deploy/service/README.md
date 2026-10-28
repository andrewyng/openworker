# OpenWorker Service on a local machine

Run the API, controller, worker and PostgreSQL in Docker Compose. `ows` connects
through HTTPS and submits agents to an existing NVIDIA OpenShell gateway.
This reference is for local integration testing. It does not install Kubernetes
or OpenShell, and it is not a production HA deployment.

For the native `ows deploy local` Kubernetes installer, including pinned OpenShell
installation or reuse of an existing gateway, see the
[local installation guide](../../docs/harness/OWS-LOCAL-INSTALLATION.md).

```text
ows / application SDK
        │ HTTPS + bearer token; REST requests and SSE events
        ▼
TLS ingress ── API ── PostgreSQL
                         ▲
                 controller + worker
                         │ gateway mTLS
                         ▼
              NVIDIA OpenShell on Kubernetes
                  sandboxed ToolRunner
```

Only the HTTPS port is published, on host loopback. PostgreSQL uses TLS on a
private Docker network. Gateway certificates are available to the controller and
worker; the API has none. The controller creates/deletes environments. Model keys
are mounted only into the worker. Runtime roles run as non-root users, without a
Docker socket. A short-lived root installer populates dedicated credential volumes.

## Prerequisites

- Docker with Compose v2, Node.js 20+, OpenSSL, and the repository's Rust toolchain
  for a host-native CLI.
- A working OpenShell gateway with client mTLS credentials, reachable **from the
  containers**, plus a pinned runner image available to its Kubernetes nodes.
- A matching Linux `openworker` runner binary. It must match the executable in the
  runner image and the installed tool bundle; an arbitrary image is insufficient.
  See [OpenShell setup](../../harness/server-reference/README.md).

On Docker Desktop, `host.docker.internal` reaches the host. With Linux Docker,
`host-gateway` is configured for execution roles, but a host listener bound only
to `127.0.0.1` is generally not reachable from a bridge container. Use a gateway
address reachable from Docker and retain certificate-name verification. Do not
expose an unauthenticated gateway or disable TLS verification to make it connect.

## Build

From the repository root:

```sh
cargo build --manifest-path harness/Cargo.toml --locked --release -p openworker-service-cli
docker build --target runtime -f deploy/service/Dockerfile -t openworker-service:local .
```

The host CLI is `harness/target/release/ows`. The image includes Linux `ows`, the
service executable and the runtime. Base image digests and the Cargo lockfile are
pinned; system package downloads still use the distribution repository. Build on
the intended architecture. This milestone was qualified on ARM64, not x86-64.

For development with already-built **Linux** binaries, put `ows`,
`openworker-server-reference` and `openworker` in `.ow-service-build/out/` and use
`--target prebuilt`. Never copy host macOS binaries into that directory.
The Docker build context excludes generated state and credentials.

## Configure and start

Copy [connection.example.json](connection.example.json) to a private local file.
Supply the gateway endpoint, TLS server name, certificate directory containing
`ca.crt`, `tls.crt`, `tls.key`, matching runner path, and image digest. The example
uses `scripted: true`: it exercises orchestration with deterministic model outputs
and real tools, without a model key.

For live model use, set `scripted: false`, `modelProvider` to `openai` or
`anthropic`, `model` to an available model ID, and `modelKeyFile` to a private file
containing **only the key**, not an environment assignment. The file is copied to
local private state and a worker-only volume. This installer does not yet configure
custom provider endpoints or hot credential rotation.

```sh
node deploy/service/stack.mjs init .ow-service /absolute/connection.json openworker-service:local
node deploy/service/stack.mjs up .ow-service
harness/target/release/ows --config .ow-service/cli.json whoami
```

Initialization creates a unique deployment/service ID, local CA, short-lived server
certificates, random PostgreSQL password and an admin bootstrap token with a
one-hour default lifetime. The token is
installed through a private one-time job, never an unauthenticated API call. Keep
`.ow-service/` private and out of version control. The generated CLI context pins
the service identity and CA and stores a token-file reference, not the token.
Default URL: `https://localhost:58443`; pass another port as the last `init` argument.

`up` waits for containers and PostgreSQL, but the subsequent `whoami` is the
application-level readiness check. If startup fails, use `status` and inspect only
this deployment's container logs. The installer withholds captured subprocess
output when it might contain credentials. Do not paste state/config files into logs.

## Deploy and submit an agent

See the [CLI quickstart](../../docs/harness/SERVICE-M3-QUICKSTART.md) for profile and
agent definitions, submission, approvals, questions and cancellation. Deployment
publishes immutable JSON/YAML definitions selecting tools already installed in the
image. It does not upload native code or arbitrary tool servers.

The default bundle contains `read_file`, `write_file`, and `run_shell`. In this
milestone the OpenShell workspace is `/sandbox` and the execution network policy is
network-deny. Approval bypass does not disable sandbox policy.

## End-to-end qualification

Against a scripted deployment and a real local OpenShell gateway:

```sh
node deploy/service/qualify.mjs .ow-service harness/target/release/ows
```

This submits real file operations, answers approvals/questions, rejects wrong TLS
trust and credentials, verifies idempotency conflicts, restarts API/worker, stops
and starts the full stack with retained volumes, cancels a task, closes the session
and deletes its sandbox. A passing run writes `.ow-service/qualification.json`.
It restarts this deployment: use a dedicated instance, not a shared working service.
A failed run retains evidence and resources for inspection; it does not blindly
retry uncertain tool actions or destroy the database.

## Stop, resume, remove

```sh
node deploy/service/stack.mjs status .ow-service
node deploy/service/stack.mjs down .ow-service
node deploy/service/stack.mjs up .ow-service
```

`down` preserves database/credential volumes and remote environments. Image identity
is pinned at initialization; retagging a changed image cannot silently upgrade an
existing deployment. This milestone has no automated image-upgrade command.

Before destruction, cancel/finish runs, close sessions and explicitly delete each
environment using `ows`. Deletion is asynchronous: repeat with the **same** key to
observe it, or use `environment get`. The controller dispatches deletion once;
after connection loss it only checks the original object's state. A permanently
unknown outcome requires operator reconciliation, not a new delete request.

```sh
ows --config .ow-service/cli.json environment list
node deploy/service/stack.mjs destroy .ow-service THE_EXACT_PRINTED_DEPLOYMENT_ID
```

Destruction refuses while owned environments remain. It removes only this Compose
project's containers/volumes; OpenShell and Kubernetes remain untouched. The local
state directory and credentials remain for deliberate operator disposal. Once
destruction starts, the deployment identity cannot be restarted against an empty
database. An interrupted destruction can be resumed with the same exact ID.

Expiring service tokens, revisioned configuration and Kubernetes pool management
are covered by the [M4 guide](../../docs/harness/SERVICE-M4-QUICKSTART.md).
Hosted SSO and cloud qualification follow later. Model connections/secrets and
worker-slot settings currently remain deployment configuration.

Cloud installation uses the same native installer through `ows deploy cluster`.
See the [experimental AWS lifecycle guide](../../docs/harness/OWS-AWS-LIFECYCLE.md)
for planning, guard checks, image publication, private Secret handoff and recovery.
It is locally qualified; AWS IAM/network/storage/expiry behavior still needs the
explicitly authorized live trial.
