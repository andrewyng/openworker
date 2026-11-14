# Local backup and recovery

Restore PostgreSQL session state and published artifacts into a fresh, isolated
deployment before allowing work to run. Database recovery does not restore sandbox
files, prove external actions did not happen, or fence old workers. The existing
live Task Desk deployment remains untouched by this drill.

## Recovery mode

Set `"recovery_read_only": true` in the server reference configuration. The default
is false, preserving existing deployments and Desktop/SDK behavior.

- Authenticated session snapshots, event replay and artifact reads work.
- HTTP mutations return `503 {"error":"recovery_read_only"}`, including tasks,
  turns, approvals, cancellation, credential changes and artifact uploads. Clients
  should stop sending mutations, not automatically retry them.
- Worker, environment-create and environment-reconcile CLI commands refuse before
  connecting to PostgreSQL. API inspection skips provider initialization and needs
  no gateway/model credentials. `/readyz` identifies the recovery mode; readiness
  means inspection is available, not that execution or the sandbox is healthy.
- Trusted credential-administration CLI commands remain available to revoke or
  replace restored keys. This API guard is not a PostgreSQL read-only role or
  protection against a database administrator.

With Helm, set `config.recovery_read_only: true`, `worker.replicas: 0`, and remove
`secrets.model`. The chart rejects active workers/model credentials and omits
OpenShell credential mounts. Use separate infrastructure/network isolation too;
the flag is not a kernel sandbox or a distributed deployment fence.

## Archive contents and limits

The archive has a custom-format PostgreSQL schema dump, the corresponding artifact
catalog, immutable manifest/chunk objects for every published artifact, and a
completion manifest with byte counts and SHA-256 hashes. It becomes valid only
after every referenced published artifact verifies. Limits: 64 MiB dump, 128 MiB
total, 10,000 catalog entries. This is a small reference, not a large backup engine.
Missing/corrupt files, duplicate keys, traversal and symlinks are rejected.

Pending uploads stay pending; unpublished partial objects are not copied or
promoted. Restoring the database alone does not make artifacts available. A missing
published object invalidates the backup instead of silently skipping that artifact.

Storage identity includes deployment, bucket, region and logical endpoint. This
drill preserves them, resolving the same service DNS name to new isolated storage.
Credentials are regenerated. Never rewrite catalog bindings simply to make a
download work; relocation to a different logical endpoint/bucket needs a separate,
audited design.

Snapshots include conversation content, grants, pending commands, audit records
and credential hashes. Messages may contain user secrets. Archive files are private
but **not encrypted or signed**: checksums detect corruption, not malicious manifest
replacement. Production needs encrypted offsite storage, trusted manifests, access
controls and retention. Deployment/model secrets, TLS keys, provider registrations,
bundle releases and sandbox volumes have separate recovery procedures.

PostgreSQL `pg_dump` captures a consistent snapshot without stopping clients.
Restore uses `pg_restore --single-transaction --exit-on-error --no-owner
--no-privileges` into an empty database. Dumps can execute SQL chosen by the source
administrator; accept only trusted backups. See the official
[dump](https://www.postgresql.org/docs/16/app-pgdump.html) and
[restore](https://www.postgresql.org/docs/16/app-pgrestore.html) documentation.
Retain immutable published objects while copying the snapshot's references;
coordinate artifact deletion/retention with backups.

## Run the local drill

Requires the existing local Task Desk Helm fixture and explicit kind kubeconfig,
local Docker Unix socket, Node 20.19+, curl with SigV4 and previously pinned images.
No AWS or model calls. This helper is deliberately limited to this development
fixture; it is not a generic customer backup command.

```sh
docker exec -w /work/harness ow-harness-controls-runner-20261011 env CARGO_BUILD_JOBS=1 CARGO_TARGET_DIR=/build cargo build --locked -p openworker-server-reference
docker exec -w /work/harness ow-harness-controls-runner-20261011 env CARGO_BUILD_JOBS=1 CARGO_TARGET_DIR=/build cargo build --locked -p openworker-store-postgres --features test-support --bin pg-recovery-fixture
node --test deploy/recovery/backup.test.mjs deploy/helm/chart.test.mjs
node deploy/recovery/local.mjs
```

The script reads the source schema and restores it into a fresh Docker project.
Three labelled synthetic domain fixtures add a pending approval, question and
uncertain started tool; a fourth fixture is a revoked key. These are inserted only
in the copy using the harness store. The script backs that copy up and restores
it into a second fresh project. No synthetic tool executes or original session
is modified. Source workers remain on their original database throughout.

Database, object store and API use internal-only Docker networks, without model/
gateway credentials, host workspace mounts or Docker sockets. A separate TLS proxy
publishes ephemeral ports only on 127.0.0.1. Proxy certificates are copies of local
fixture certificates, stored outside the archive. TLS/hostname verification stays
on for API, database and object-store connections.

Private evidence lives under `.ow-recovery/<run-id>/`: original dump, completed
original/qualified archives, expected snapshots, diagnostics and `qualification.json`.
A failed run does not write a successful qualification report. Cleanup removes
only the exact owned Compose projects/volumes, never global Docker data or the
original namespace. Private archives/evidence remain; never commit or send keys.

## Cutover procedure

1. Fence the old deployment: stop/revoke old API/worker/provider access and inspect
   already-started external actions. Two databases do not share lease fencing;
   copying an owner epoch cannot stop an old worker using the original database.
2. Restore verified data into fresh storage. Keep workers at zero, provider
   credentials absent and inspection private/read-only. Never overwrite an active
   database or use automatic migrations to hide a version mismatch.
3. Compare session/turn/interaction IDs, policy digests, event cursors, artifacts,
   credential state and command receipts. Pending decisions stay pending. Reconcile
   unknown effects against their original environment/system, not by replaying them.
4. Reapply revocations, rotation and security-policy changes since the snapshot.
   Otherwise an old backup may resurrect a revoked key. Review session approval
   grants and accepted controls; permission is not evidence an effect is safe to repeat.
5. Recover sandbox files separately and verify the original provider/object identity.
   An absent sandbox blocks execution; do not substitute a blank one behind the same
   session. Database and volume snapshots may represent different times.
6. After operator review, deliberately enable execution, supply current credentials,
   switch clients once and monitor. Never run original and restored workers together.

An effect completed after the snapshot can appear unstarted in restored records.
Exactly-once execution and zero data loss are not promised. External idempotency
keys, effect inspection and an operator's cutover decision remain necessary.
This drill does not qualify PITR/WAL archiving, offsite retention, sandbox-volume
restoration, cross-version upgrades or complete production cutover. The domain
fixture proves pending decisions survive and recorded started effects become
unknown; it does not claim to reconcile a real external effect.
