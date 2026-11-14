# Upgrade stored sessions safely

Back up your application state before changing its SDK or native runtime.

Install the runtime selected by the SDK package. For a shared service, deploy
compatible API and worker builds together. See [release versions](releases.md).

## SQLite applications

The attachment-capable runtime upgrades supported schema 7/8 databases to **9**.
The application state lock prevents concurrent runtimes from opening the same
state directory. Older binaries refuse schema 9. Keep a pre-upgrade backup if you
need to restore the previous application; replacing the executable alone is not
a database downgrade.

## PostgreSQL services

Execution storage uses schema **7**. Stop admission, drain workers and stop old
API/SDK processes before the explicit migration. Allow execution leases to finish
or expire; do not delete ownership records to force an upgrade. Use the migration
command documented for your matching [service build](../../../deploy/service/README.md).

Old workers are fenced from writing the new schema. Do not change a schema marker
manually to make them start. Restore a reviewed backup for rollback rather than
rewriting runtime metadata.

## Recording storage

Observability uses its own schema and connection. Run its explicit migration when
using PostgreSQL recording, even if it shares the same PostgreSQL server as
execution storage. Follow [recording configuration](observability.md).

## Check after an upgrade

Resume a saved session, submit a follow-up, check tools/approvals and read any
stored attachments. Verify recording/export health when enabled. Preserve the
previous app package and backup until your application checks pass.
