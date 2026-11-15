import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
import { guard } from "./guard.mjs";
import { infra, name } from "./templates.mjs";
const code = await readFile(new URL("cleanup.cjs", import.meta.url), "utf8");
async function run({
  expiry = "2000-01-01",
  stack,
  absent = false,
  account = "123456789012",
  volumes = [],
} = {}) {
  const seen = [];
  const mocks = new Proxy(
    {},
    {
      get: (_, key) =>
        String(key).endsWith("Client")
          ? class {
              async send(command) {
                seen.push(command);
                if (command.kind === "DescribeStacksCommand") {
                  if (absent)
                    throw Object.assign(Error("does not exist"), {
                      name: "ValidationError",
                    });
                  return {
                    Stacks: [
                      stack || {
                        StackId: "only-this-stack",
                        StackStatus: "CREATE_COMPLETE",
                        Tags: [{ Key: "Project", Value: name }],
                      },
                    ],
                  };
                }
                if (command.kind === "ListObjectsV2Command") return {};
                if (command.kind === "ListMultipartUploadsCommand") return {};
                if (command.kind === "DescribeVolumesCommand")
                  return { Volumes: volumes };
                return {};
              }
            }
          : class {
              constructor(input) {
                this.input = input;
                this.kind = key;
              }
            },
    },
  );
  const ctx = {
    require: () => mocks,
    exports: {},
    console: { log() {} },
    Date,
    process: {
      env: {
        EXPERIMENT: name,
        ACCOUNT: account,
        BUCKET: `${name}-${account}`,
        EXPIRY: expiry,
      },
    },
  };
  vm.runInNewContext(code, ctx);
  const result = await ctx.exports.handler(
    {},
    {
      invokedFunctionArn: "arn:aws:lambda:us-west-2:123456789012:function:test",
    },
  );
  return { result, seen };
}
test("expiry guard cannot act before deadline or on another account", async () => {
  const r = await run({ expiry: "2099-01-01" });
  assert.equal(r.seen.length, 0);
  assert.equal(r.result.status, "not_due");
  await assert.rejects(run({ account: "111111111111" }), /identity mismatch/);
});
test("expiry guard refuses an existing untagged stack", async () => {
  await assert.rejects(
    run({ stack: { Tags: [], StackStatus: "CREATE_COMPLETE" } }),
    /unowned/,
  );
});
test("expiry guard targets exact stack ID and only detached tagged volumes", async () => {
  const r = await run();
  assert.deepEqual(
    JSON.parse(
      JSON.stringify(r.seen.find((x) => x.kind === "DeleteStackCommand").input),
    ),
    { StackName: "only-this-stack" },
  );
  const filters = r.seen.find((x) => x.kind === "DescribeVolumesCommand").input
    .Filters;
  assert.equal(filters[0].Values[0], name);
  assert.equal(filters.length, 1);
});
test("cleanup stops schedule only once stack and owned detached volumes are absent", async () => {
  const r = await run({ absent: true });
  assert.equal(r.seen.at(-1).kind, "DisableRuleCommand");
  assert.equal(r.seen.at(-1).input.Name, name + "-cleanup");
});
test("an attached owned volume keeps cleanup scheduled until detachment", async () => {
  const r = await run({
    absent: true,
    volumes: [{ VolumeId: "vol-owned", State: "in-use" }],
  });
  assert.ok(
    !r.seen.some(
      (x) =>
        x.kind === "DisableRuleCommand" || x.kind === "DeleteVolumeCommand",
    ),
  );
  const detached = await run({
    absent: true,
    volumes: [{ VolumeId: "vol-owned", State: "available" }],
  });
  assert.ok(detached.seen.some((x) => x.kind === "DeleteVolumeCommand"));
  assert.ok(!detached.seen.some((x) => x.kind === "DisableRuleCommand"));
});
test("infra has no workload ingress, no NAT, bounded nodes and private encrypted database", () => {
  const t = infra({
    expiry: "2099-01-01",
    operatorCidr: "192.0.2.1/32",
    cniVersion: "x",
    podIdentityVersion: "y",
    ebsVersion: "z",
    pgVersion: "16.10",
  });
  assert.equal(t.Resources.Nodes.Properties.ScalingConfig.MaxSize, 2);
  assert.equal(t.Resources.Database.Properties.PubliclyAccessible, false);
  assert.equal(t.Resources.Database.Properties.StorageEncrypted, true);
  assert.equal(t.Resources.Database.DeletionPolicy, "Delete");
  assert.equal(
    t.Resources.Cluster.Properties.ResourcesVpcConfig.PublicAccessCidrs[0],
    "192.0.2.1/32",
  );
  assert.ok(!JSON.stringify(t).includes("AWS::EC2::NatGateway"));
  const savedEmail = process.env.OW_AWS_BUDGET_EMAIL;
  process.env.OW_AWS_BUDGET_EMAIL = "test@example.com";
  const g = guard({ expiry: "2099-01-01", code });
  if (savedEmail === undefined) delete process.env.OW_AWS_BUDGET_EMAIL; else process.env.OW_AWS_BUDGET_EMAIL = savedEmail;
  assert.equal(g.Resources.Cleanup.Properties.ReservedConcurrentExecutions, 1);
  assert.ok(!JSON.stringify(g).includes("AdministratorAccess"));
});
