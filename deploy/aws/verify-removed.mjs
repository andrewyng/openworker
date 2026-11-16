// Read-only final audit. Never treats a denied/failed AWS query as absence.
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { connect, account } from "./client.mjs";
import { name } from "./templates.mjs";
import { repo } from "../helm/local.mjs";
const { aws } = await connect();
const checks = [
  [
    "stacks",
    ["cloudformation", "describe-stacks"],
    (r) => r.Stacks.map((x) => x.StackName),
  ],
  ["clusters", ["eks", "list-clusters"], (r) => r.clusters],
  [
    "databases",
    ["rds", "describe-db-instances"],
    (r) => r.DBInstances.map((x) => x.DBInstanceIdentifier),
  ],
  ["buckets", ["s3api", "list-buckets"], (r) => r.Buckets.map((x) => x.Name)],
  [
    "repositories",
    ["ecr", "describe-repositories"],
    (r) => r.repositories.map((x) => x.repositoryName),
  ],
  [
    "functions",
    ["lambda", "list-functions"],
    (r) => r.Functions.map((x) => x.FunctionName),
  ],
  [
    "rules",
    ["events", "list-rules", "--name-prefix", name],
    (r) => r.Rules.map((x) => x.Name),
  ],
  [
    "logs",
    [
      "logs",
      "describe-log-groups",
      "--log-group-name-prefix",
      "/aws/lambda/" + name,
    ],
    (r) => r.logGroups.map((x) => x.logGroupName),
  ],
  ["roles", ["iam", "list-roles"], (r) => r.Roles.map((x) => x.RoleName)],
  [
    "budgets",
    ["budgets", "describe-budgets", "--account-id", account],
    (r) => r.Budgets.map((x) => x.BudgetName),
  ],
  [
    "vpcs",
    ["ec2", "describe-vpcs", "--filters", `Name=tag:Project,Values=${name}`],
    (r) => r.Vpcs.map((x) => name + ":" + x.VpcId),
  ],
  [
    "volumes",
    ["ec2", "describe-volumes", "--filters", `Name=tag:Project,Values=${name}`],
    (r) => r.Volumes.map((x) => name + ":" + x.VolumeId),
  ],
  [
    "instances",
    [
      "ec2",
      "describe-instances",
      "--filters",
      `Name=tag:Project,Values=${name}`,
    ],
    (r) =>
      r.Reservations.flatMap((x) => x.Instances)
        .filter((x) => x.State.Name !== "terminated")
        .map((x) => name + ":" + x.InstanceId),
  ],
];
const remaining = {};
// Bound concurrency and preserve any error rather than report partial success.
for (let i = 0; i < checks.length; i += 4) {
  await Promise.all(
    checks.slice(i, i + 4).map(async ([kind, args, names]) => {
      remaining[kind] = names(await aws(args)).filter((x) => x.includes(name));
    }),
  );
}
const report = {
  time: new Date().toISOString(),
  account,
  region: "us-west-2",
  remaining,
  complete: Object.values(remaining).every((x) => x.length === 0),
};
await writeFile(
  join(repo, ".ow-aws/final-removal-audit.json"),
  JSON.stringify(report, null, 2),
  { mode: 0o600 },
);
console.log(JSON.stringify(report));
if (!report.complete) process.exitCode = 1;
