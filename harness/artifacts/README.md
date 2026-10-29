# Durable artifacts

Selected files survive the worker and sandbox that produced them. PostgreSQL
owns the session/turn catalog; this crate owns immutable bytes. Neither a tool
completion nor a successful upload alone means the artifact is ready.

```text
App selects output → register pending ID in PostgreSQL
                              ↓
                    stream to configured store
                    verify size + SHA-256
                              ↓
                  ready metadata + event + audit
                    (one PostgreSQL transaction)
                              ↓
                   download through any API
```

This server slice does not alter Desktop, local SQLite, stdio or Harness SDK
protocols. The byte-store crate can be embedded independently. It does not scan
the workspace, upload every tool result, or automatically give a model an upload
tool. A trusted application or explicitly authorized custom tool chooses the
output. Existing sandbox filesystem/egress restrictions remain in force.

## Storage configuration

Add one `artifacts` object to the reference server configuration. All API nodes
must use the same deployment/store. Run the existing explicit `migrate` command
to install optional artifact catalog extension v1. Base schema stays v5, with
no session rewrite/reset; existing sessions still work without the extension.

For a private S3 bucket:

```json
{"artifacts":{"kind":"s3","deployment":"my-product","bucket":"my-private-artifacts","region":"us-east-1"}}
```

AWS regional endpoint is the default. An optional explicit `endpoint` is an
HTTPS origin; plaintext is accepted only for loopback qualification servers.
URLs containing credentials, query strings or paths are rejected. TLS is
verified. v1 bucket names are lowercase letters/digits/hyphens, without dots.
Private HTTPS endpoints may set `ca_file` to an explicit PEM CA bundle (maximum
64 KiB); malformed/empty bundles fail closed. This adds a trust anchor without
turning off hostname or certificate validation. Rotate via an explicit API
restart. The local Helm qualification exercises this against TLS-fronted S3.
The runtime does not create buckets or infrastructure.

Credentials are configured on the API process, never in coworker bundles,
HTTP messages, sandbox tools, audit or telemetry. Supported client inputs are
environment access key/secret/session token, web-identity role/token-file,
container credential inputs, or default instance metadata credentials. Trusted
embedders may inject an `AwsCredentialProvider`. Profile/SSO login is not an
integration promise here. Rotation does not change storage identity. Give the
role only required GetObject/PutObject access to its deployment prefix; keep
public access disabled. Encryption, lifecycle, backup, IAM and quotas remain
operator responsibilities. Native AWS IAM/STS/KMS behavior needs cloud
qualification; tests here use a local S3-compatible server and synthetic keys.

For EKS Pod Identity, use the service's associated Kubernetes service account.
EKS supplies `AWS_CONTAINER_CREDENTIALS_FULL_URI` and
`AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`; do not supply static AWS keys. Both
inputs must be present together. An incomplete pair fails configuration instead
of falling through to instance metadata. Missing/rejected token files fail the
transfer explicitly. The credential provider rereads the projected token file
when refreshing temporary credentials; rotation does not require changing the
artifact's stored backend binding.

For a shared durable filesystem:

```json
{"artifacts":{"kind":"filesystem","deployment":"my-product","root":"/mnt/shared/ow-artifacts","shared_durable":true}}
```

Create a dedicated service-owned directory beforehand. The reference refuses
worker-local fallback: `shared_durable` is a required operator assertion, not a
hardware guarantee. All API nodes need the **same** filesystem; backend binding
pins a persisted store UUID. Unix roots cannot be group/world-writable or a
symlink. Capability-relative, generated names prevent user-path traversal;
object symlinks are rejected. Filesystem writes require atomic hard links and
durable file/directory synchronization; qualify the actual shared filesystem.
An embedding single-node app may explicitly select local mode.

## API and small Node example

All routes inherit reference bearer authentication, Origin rejection and the
configured principal. No public links or presigned URLs. This is single-principal
reference authorization, **not tenant isolation**; production hosts must enforce
principal/session access before these trusted operations.

Managed OpenWorker Service exposes these same operations under
`/v1/sessions/:sid/artifacts`. Send the service bearer credential and
`X-OpenWorker-Namespace` header, as for other `/v1` requests. Registration,
upload and reconciliation require `artifact.write`; listing, metadata and download
require session-scoped read authority. Legacy unversioned routes remain disabled
unless compatibility mode was explicitly enabled. `/v1/capabilities` advertises
`features.artifacts` only when storage is configured.

| Request | Purpose |
|---|---|
| `POST /sessions/:sid/artifacts` | Register `{request_id,turn_id,name,content_type,size,sha256}`; returns pending ID. Exact retry returns the same ID, including after ready. Changed actor/payload/store is 409. |
| `GET /sessions/:sid/artifacts` | List bounded session metadata. |
| `GET /sessions/:sid/artifacts/:id` | Inspect pending/ready metadata. |
| `PUT /sessions/:sid/artifacts/:id/content` | Raw streamed bytes; checks expected size/hash, then publishes verified ready metadata. |
| `POST /sessions/:sid/artifacts/:id/reconcile` | Read and verify the same stored bytes; finish a lost ready commit without rerunning a tool. |
| `GET /sessions/:sid/artifacts/:id/content` | Ready-only, authenticated attachment stream. Pinned manifest and each chunk are validated; corruption aborts delivery. |

```sh
export OW_API_URL=http://127.0.0.1:8081
export OW_API_TOKEN_FILE=/private/api-token
node server-reference/artifact-client.mjs --session SESSION_ID --turn TURN_ID \
  --file /explicit/output.zip --request-id upload-001
```

The Node 20+ example hashes and streams a caller-selected regular file, printing
the request/artifact IDs before transfer. To recover response loss, inspect the
same ID or rerun with the **same unchanged file/request/session/turn**, optionally
against another API node. A new request ID is a new artifact, not reconciliation.
Do not regenerate a file or repeat a consequential tool just because upload
status is unknown. The HTTP interface never accepts a client-authored ready
proof, storage key, bucket or host path. Trusted Rust embedders must likewise call
`Store::put`/`verify` before `PostgresStore::publish_artifact`.

## Failure behavior and bounds

- 64 MiB per artifact; 256 registered artifacts per session; four active content
  transfers/reconciliations per API node. No global admission/byte quota yet.
- 1 MiB immutable chunk objects, plus a final manifest. S3 keys are generated
  `deployment/session/artifact/part-NN` and `/manifest`. Filesystem object names
  hash those keys. This is not a single raw S3 object or multipart upload.
- Atomic create-if-absent and exact byte comparison make identical retries safe;
  different bytes never overwrite. Wrong-checksum uploads stay pending and can
  leave conflicting chunks: explicitly register a new artifact after inspecting
  the failed upload, rather than changing its expected hash in place.
- Interrupted input may leave complete chunks without a manifest. Retry the
  identical original bytes. A complete manifest with pending metadata can be
  reconciled. Reconciliation reads all chunks, not just an object HEAD.
- Ready metadata, durable `artifact_ready` event and audit are atomic in PG.
  Object storage and PG are **not** one transaction. Missing/corrupted content
  fails; the service does not recreate it or silently mark pending data ready.
- Store I/O/input reads have 20-second bounds; HTTP upload/reconcile has a
  120-second total limit. S3 has a 15-second client timeout and no automatic
  retries. Cancellation stops waiting; a previously issued filesystem/network
  write can still finish. Reconcile immutable IDs after unknown outcomes.
- Memory is chunk-bounded, not artifact-sized. Download verifies each chunk
  before yielding it, but cannot retract earlier bytes if a later chunk fails.
  Consumers should verify the advertised full size/hash before accepting output.
- Display name/type/checksum live in operational metadata; durable events/audit
  contain IDs/status, not filename or content. Downloads use attachment,
  nosniff, no-store and restrictive CSP, never inline preview.

No artifact deletion, automatic orphan cleanup, retention, export links, tenant
quotas or public hosting in this slice. Customers own lifecycle/cleanup for
incomplete prefixes and stale temporary files; preserve ready objects and the
filesystem `store-identity`. Storage-admin tampering and snapshots/backups are
outside the capability-root threat boundary. Windows/shared network filesystems
and actual AWS deployments were not qualified by these local tests.

## Tests

`cargo test --locked -p openworker-artifacts --all-features` exercises filesystem
streaming, cancellation, corruption and path protections. Opt-in S3 uses
`OW_TEST_S3_ENDPOINT`, synthetic AWS environment credentials and a disposable
`openworker-artifacts` bucket, with `-- --ignored`. Catalog tests use isolated
`OW_PG_TEST_URL`: `cargo test -p openworker-store-postgres --test artifacts --
--ignored`. Never point these tests at customer infrastructure.

The server `replicas` test uses PostgreSQL, two API processes, two workers and
real remote OpenShell tools. It publishes a generated/read-back file, streams a
larger binary, reconciles a lost ready commit and downloads after API/worker
replacement. Set `OW_TEST_S3_ENDPOINT` for S3 instead of shared filesystem and
`OW_PROBE_NODE` to exercise both shipped Node clients. The model is scripted;
the storage, processes, sandbox and file operations are real.

A focused storage test needs no gateway or live model. With the pinned PostgreSQL
and SeaweedFS images from `deploy/reference/compose.yaml` cached locally, and
`OW_PROBE_RUNNER` pointing to an existing Linux ELF runner (validated by the API
profile but never executed in this test):

```sh
node deploy/reference/qualify-artifact-storage.mjs
```

This creates isolated loopback Docker fixtures, runs the immutable S3 contract and
the HTTP/catalog test for both shared filesystem and S3, then removes only its
labeled containers. It verifies truncated upload, exact-ID retry, lost-ready
reconciliation, cross-API download and replacement of both original API processes.
Private logs and generated fixture credentials remain in the printed state folder;
never commit them. Local credential-chain tests additionally use a synthetic
projected-token endpoint and signed loopback S3 fixture to check token refresh and
fail-closed behavior, in child processes without inherited AWS credentials.

The opt-in `pod_identity_access` example and
`deploy/reference/Dockerfile.artifact-probe` are cloud qualification helpers, not
part of the shipped service image. Run only against owned disposable resources:
the probe conditionally attempts one-byte writes in explicitly recorded foreign
test prefixes and requires actual AccessDenied responses, rather than treating
timeouts or missing objects as proof of authorization.
