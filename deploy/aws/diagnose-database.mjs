// Read-only SQL inspection after an uncertain migration; never resets schema.
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { connect } from "./client.mjs";
import { name } from "./templates.mjs";
import { repo, kubectl } from "../helm/local.mjs";
const { aws } = await connect(),
  m = JSON.parse(await readFile(join(repo, ".ow-aws/kubernetes.json"), "utf8"));
if (m.context !== name) throw Error("Wrong cluster");
const db = (
  await aws(["rds", "describe-db-instances", "--db-instance-identifier", name])
).DBInstances[0];
const secret = JSON.parse(
  (
    await aws([
      "secretsmanager",
      "get-secret-value",
      "--secret-id",
      db.MasterUserSecret.SecretArn,
    ])
  ).SecretString,
);
const namespace = "ow-app",
  resource = "ow-database-diagnostic";
await kubectl(
  m,
  ["create", "-f", "-"],
  JSON.stringify({
    apiVersion: "v1",
    kind: "Secret",
    metadata: { name: resource, namespace },
    type: "Opaque",
    stringData: {
      PGHOST: db.Endpoint.Address,
      PGPORT: "5432",
      PGDATABASE: "openworker",
      PGUSER: secret.username,
      PGPASSWORD: secret.password,
      PGSSLMODE: "verify-full",
      PGSSLROOTCERT: "/ca/ca.crt",
      PGCONNECT_TIMEOUT: "10",
      PGOPTIONS:
        "-c default_transaction_read_only=on -c statement_timeout=5000",
    },
  }),
);
await kubectl(
  m,
  ["create", "-f", "-"],
  JSON.stringify({
    apiVersion: "batch/v1",
    kind: "Job",
    metadata: { name: resource, namespace },
    spec: {
      backoffLimit: 0,
      activeDeadlineSeconds: 45,
      template: {
        metadata: {
          labels: {
            "app.kubernetes.io/name": "openworker",
            "app.kubernetes.io/instance": "ow",
            "app.kubernetes.io/component": "worker",
          },
        },
        spec: {
          restartPolicy: "Never",
          automountServiceAccountToken: false,
          securityContext: { fsGroup: 1000 },
          containers: [
            {
              name: "inspect",
              image:
                "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
              command: [
                "psql",
                "-X",
                "-w",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "SELECT version();",
                "-c",
                "SELECT nspname FROM pg_namespace WHERE nspname='ow_taskdesk';",
                "-c",
                "SELECT tablename FROM pg_tables WHERE schemaname='ow_taskdesk' ORDER BY tablename;",
              ],
              envFrom: [{ secretRef: { name: resource } }],
              securityContext: {
                runAsNonRoot: true,
                runAsUser: 1000,
                allowPrivilegeEscalation: false,
                readOnlyRootFilesystem: true,
                capabilities: { drop: ["ALL"] },
              },
              volumeMounts: [{ name: "ca", mountPath: "/ca", readOnly: true }],
            },
          ],
          volumes: [{ name: "ca", secret: { secretName: "database-ca" } }],
        },
      },
    },
  }),
);
try {
  await kubectl(m, [
    "-n",
    namespace,
    "wait",
    "--for=condition=complete",
    "job/" + resource,
    "--timeout=60s",
  ]);
} catch {}
const output = await kubectl(m, ["-n", namespace, "logs", "job/" + resource]);
await writeFile(join(repo, ".ow-aws/database-diagnostic.txt"), output, {
  mode: 0o600,
});
console.log(output);
console.log(
  "Diagnostic Job/Secret retained for inspection; remove these exact owned resources after recording the result.",
);
