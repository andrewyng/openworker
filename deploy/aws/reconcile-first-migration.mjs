// Explicit one-time recovery after the recorded read-only schema inspection.
// Never used by install/bootstrap automatically, never deletes database data.
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { repo, kubectl } from "../helm/local.mjs";
import { name } from "./templates.mjs";
const m = JSON.parse(
  await readFile(join(repo, ".ow-aws/kubernetes.json"), "utf8"),
);
if (m.context !== name) throw Error("Wrong cluster");
const args = ["-n", "ow-app"];
const old = JSON.parse(
  await kubectl(m, [...args, "get", "job", "ow-migrate", "-o", "json"]),
);
const diagnostic = JSON.parse(
  await kubectl(m, [
    ...args,
    "get",
    "job",
    "ow-database-diagnostic",
    "-o",
    "json",
  ]),
);
if (
  !old.status.failed ||
  old.status.active ||
  diagnostic.status.succeeded !== 1
)
  throw Error("Need failed migration and completed read-only diagnostic");
const output = await kubectl(m, [
  ...args,
  "logs",
  "job/ow-database-diagnostic",
]);
if ((output.match(/\(0 rows\)/g) || []).length !== 2)
  throw Error("Schema absence was not established; manual inspection required");
const logs = await kubectl(m, [
  ...args,
  "logs",
  "job/ow-migrate",
  "-c",
  "admin",
]);
await writeFile(
  join(repo, ".ow-aws/first-migration-reconciliation.json"),
  JSON.stringify(
    {
      time: new Date().toISOString(),
      failedJobUid: old.metadata.uid,
      diagnostic: output,
      migration: logs,
    },
    null,
    2,
  ),
  { mode: 0o600 },
);
await kubectl(m, [
  ...args,
  "delete",
  "job",
  "ow-migrate",
  "--wait=true",
  "--timeout=30s",
]);
console.log(
  "Recorded schema-absence evidence and removed only the failed setup Job. Database unchanged; explicit bootstrap may now be retried.",
);
