// Read-only inventory. Temporary member-account credentials stay in memory.
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const exec = promisify(execFile);
const account = process.env.OW_AWS_ACCOUNT_ID;
const roleArn = process.env.OW_AWS_ROLE_ARN;
if (!/^\d{12}$/.test(account || "") || !roleArn?.startsWith(`arn:aws:iam::${account}:role/`))
  throw Error("Set OW_AWS_ACCOUNT_ID and OW_AWS_ROLE_ARN for the authorized inventory target");
async function aws(args, env = process.env) {
  const { stdout } = await exec(
    "aws",
    [...args, "--output", "json", "--no-cli-pager"],
    {
      env: {
        ...env,
        AWS_PAGER: "",
        AWS_RETRY_MODE: "standard",
        AWS_MAX_ATTEMPTS: "2",
      },
      timeout: 60000,
      maxBuffer: 4 * 1024 * 1024,
    },
  );
  return JSON.parse(stdout);
}
const assumed = await aws([
  "sts",
  "assume-role",
  "--role-arn",
  roleArn,
  "--role-session-name",
  "ow-harness-readonly-inventory",
  "--duration-seconds",
  "3600",
]);
const c = assumed.Credentials;
const env = {
  ...process.env,
  AWS_ACCESS_KEY_ID: c.AccessKeyId,
  AWS_SECRET_ACCESS_KEY: c.SecretAccessKey,
  AWS_SESSION_TOKEN: c.SessionToken,
};
delete env.AWS_PROFILE;
delete env.AWS_DEFAULT_PROFILE;
const identity = await aws(["sts", "get-caller-identity"], env);
if (identity.Account !== account)
  throw new Error("Refusing unexpected account");
console.log(
  JSON.stringify({
    account,
    identity: identity.Arn,
    time: new Date().toISOString(),
  }),
);
const regions = (
  await aws(
    [
      "ec2",
      "describe-regions",
      "--region",
      "us-west-2",
      "--query",
      "Regions[].RegionName",
    ],
    env,
  )
).sort();
const checks = [
  [
    "instances",
    "ec2",
    "describe-instances",
    "Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType,vpc:VpcId,name:Tags[?Key==`Name`].Value|[0]}",
  ],
  [
    "vpcs",
    "ec2",
    "describe-vpcs",
    "Vpcs[].{id:VpcId,default:IsDefault,cidr:CidrBlock,name:Tags[?Key==`Name`].Value|[0]}",
  ],
  [
    "nat",
    "ec2",
    "describe-nat-gateways",
    "NatGateways[].{id:NatGatewayId,state:State,vpc:VpcId}",
  ],
  ["eks", "eks", "list-clusters", "clusters"],
  ["ecs", "ecs", "list-clusters", "clusterArns"],
  [
    "rds",
    "rds",
    "describe-db-instances",
    "DBInstances[].{id:DBInstanceIdentifier,state:DBInstanceStatus,type:DBInstanceClass,engine:Engine}",
  ],
  [
    "loadBalancers",
    "elbv2",
    "describe-load-balancers",
    "LoadBalancers[].{name:LoadBalancerName,state:State.Code,vpc:VpcId,scheme:Scheme}",
  ],
  [
    "lambda",
    "lambda",
    "list-functions",
    "Functions[].{name:FunctionName,runtime:Runtime}",
  ],
  [
    "stacks",
    "cloudformation",
    "describe-stacks",
    "Stacks[].{name:StackName,state:StackStatus}",
  ],
];
let failures = 0;
const jobs = regions.flatMap((region) =>
  checks.map((check) => ({ region, check })),
);
async function worker() {
  while (jobs.length) {
    const {
      region,
      check: [kind, service, operation, query],
    } = jobs.shift();
    try {
      const resources = await aws(
        [service, operation, "--region", region, "--query", query],
        env,
      );
      console.log(JSON.stringify({ region, kind, resources }));
    } catch (e) {
      failures++;
      // CLI errors do not include credentials; don't print spawned process/env.
      console.log(
        JSON.stringify({
          region,
          kind,
          error: String(e.stderr || "AWS query failed")
            .trim()
            .slice(0, 1000),
        }),
      );
    }
  }
}
await Promise.all(Array.from({ length: 6 }, worker));
for (const [kind, args] of [
  ["buckets", ["s3api", "list-buckets", "--query", "Buckets[].Name"]],
  [
    "budgets",
    [
      "budgets",
      "describe-budgets",
      "--account-id",
      account,
      "--region",
      "us-east-1",
      "--query",
      "Budgets[].{name:BudgetName,limit:BudgetLimit,spent:CalculatedSpend.ActualSpend,filters:CostFilters}",
    ],
  ],
]) {
  try {
    console.log(JSON.stringify({ kind, resources: await aws(args, env) }));
  } catch (e) {
    failures++;
    console.log(
      JSON.stringify({
        kind,
        error: String(e.stderr || "query failed")
          .trim()
          .slice(0, 1000),
      }),
    );
  }
}
console.log(
  JSON.stringify({
    complete: failures === 0,
    failures,
    regions: regions.length,
    note: "Selected service inventory, not an exhaustive account resource audit.",
  }),
);
process.exitCode = failures ? 1 : 0;
