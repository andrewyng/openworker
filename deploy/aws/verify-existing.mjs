// Read-only comparison for the unrelated demos in the approved member account.
// An error is not absence. The caller supplies a previously inspected baseline.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile } from "node:fs/promises";
import { connect } from "./client.mjs";
import { save } from "./qualification-lib.mjs";
const before = JSON.parse(await readFile(process.argv[2], "utf8"));
assert.equal(before.region, "us-east-1");
const { env } = await connect(),
  exec = promisify(execFile);
async function aws(args) {
  const { stdout } = await exec(
    "aws",
    [...args, "--region", "us-east-1", "--output", "json", "--no-cli-pager"],
    {
      env: { ...env, AWS_PAGER: "" },
      timeout: 60000,
      maxBuffer: 4 * 1024 * 1024,
    },
  );
  return JSON.parse(stdout);
}
const [ec2, rds, ecs] = await Promise.all([
  aws(["ec2", "describe-instances"]),
  aws(["rds", "describe-db-instances"]),
  aws(["ecs", "list-clusters"]),
]);
const instances = ec2.Reservations.flatMap((r) => r.Instances).map((i) => ({
  id: i.InstanceId,
  state: i.State.Name,
}));
const databases = rds.DBInstances.map((d) => ({
  id: d.DBInstanceIdentifier,
  state: d.DBInstanceStatus,
}));
for (const expected of before.instances)
  assert.deepEqual(
    instances.find((i) => i.id === expected.id),
    expected,
  );
for (const expected of before.rds)
  assert.deepEqual(
    databases.find((d) => d.id === expected.id),
    expected,
  );
for (const arn of before.ecs) assert.ok(ecs.clusterArns.includes(arn));
await save("existing-demos-after.json", {
  time: new Date().toISOString(),
  region: before.region,
  instances,
  rds: databases,
  ecs: ecs.clusterArns,
  baselineMatches: true,
  limit: "AWS state comparison, not application functional testing",
});
console.log(
  "PASS pre-existing demo instance/database states and ECS cluster presence match baseline",
);
