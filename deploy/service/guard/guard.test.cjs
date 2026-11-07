const { test } = require("node:test"),
  assert = require("node:assert/strict");
const { settings, run } = require("./index.cjs");
const id = "ows-0123456789abcdef0123456789abcdef",
  prefix = "ows-test-0123456789ab";
const env = {
  OWS_DEPLOYMENT_ID: id,
  OWS_ACCOUNT: "123456789012",
  OWS_REGION: "us-west-2",
  OWS_STACK_NAME: prefix + "-infra",
  OWS_BUCKET: prefix + "-123456789012",
  OWS_OBJECT_PREFIX: id + "/",
  OWS_EXECUTION_ROLE: `arn:aws:iam::123456789012:role/${prefix}-executor`,
  OWS_EXPIRY: "1000",
};
const context = {
  invokedFunctionArn: `arn:aws:lambda:us-west-2:123456789012:function:${prefix}-cleanup`,
};
function fixture() {
  const s = settings(env, context),
    tags = [{ Key: "ows:deployment-id", Value: id }],
    effects = [];
  let record = null,
    stack = {
      StackName: s.stack,
      StackId: `arn:aws:cloudformation:us-west-2:123456789012:stack/${s.stack}/original-id`,
      Tags: tags,
      StackStatus: "CREATE_COMPLETE",
    };
  const data = {
    tags,
    versioning: undefined,
    objects: [{ Key: id + "/game.html" }],
    uploads: [],
    volumes: [],
  };
  const api = {
    readControl: async () => structuredClone(record),
    writeControl: async (r, version) => {
      assert.equal(record?.version ?? null, version);
      record = structuredClone(r);
    },
    stack: async () => structuredClone(stack),
    bucket: async () => data,
    objects: async () => data.objects,
    uploads: async () => data.uploads,
    deleteObjects: async (keys) => {
      effects.push(["objects", keys]);
      data.objects = [];
    },
    abortUpload: async (u) => effects.push(["abort", u.Key]),
    deleteStack: async (arn, token) => {
      effects.push(["stack", arn, token]);
      stack.StackStatus = "DELETE_IN_PROGRESS";
    },
    volumes: async () => data.volumes,
    deleteVolume: async (v) => effects.push(["volume", v]),
  };
  return {
    s,
    api,
    data,
    effects,
    get record() {
      return record;
    },
    set stack(v) {
      stack = v;
    },
    get stack() {
      return stack;
    },
  };
}
test("scope/function identity, health and pre-expiry are non-destructive", async () => {
  for (const key of [
    "OWS_ACCOUNT",
    "OWS_BUCKET",
    "OWS_EXECUTION_ROLE",
    "OWS_STACK_NAME",
  ])
    assert.throws(() => settings({ ...env, [key]: "foreign" }, context));
  assert.throws(() => settings(env, { invokedFunctionArn: "wrong" }));
  const f = fixture();
  assert.equal(
    (await run(f.s, f.api, 2000, { mode: "health" })).status,
    "healthy",
  );
  assert.equal(f.record, null);
  assert.equal((await run(f.s, f.api, 999)).status, "not_due");
  assert.equal(f.effects.length, 0);
  assert.ok(f.record.stackArn);
});
test("expiry binds ARN and deletion token; restart observes deletion without replay", async () => {
  const f = fixture();
  assert.equal((await run(f.s, f.api, 1000)).status, "cleanup_requested");
  assert.equal((await run(f.s, f.api, 1001)).status, "deleting");
  assert.equal(f.effects.filter((e) => e[0] === "stack").length, 1);
  assert.equal(f.effects.at(-1)[2], id + "-expiry");
  f.stack = null;
  assert.equal(
    (await run(f.s, f.api, 1002)).status,
    "teardown_observed_complete",
  );
});
test("foreign objects, ownership, bucket versions and overflow block all destruction", async () => {
  for (const variant of ["foreign", "versioned", "overflow", "tags"]) {
    const f = fixture();
    if (variant === "foreign")
      f.data.objects.push({ Key: "someone-else/file" });
    if (variant === "versioned") f.data.versioning = "Suspended";
    if (variant === "overflow")
      f.data.objects = Array.from({ length: 2001 }, (_, n) => ({
        Key: id + "/" + n,
      }));
    if (variant === "tags") f.data.tags = [];
    await assert.rejects(run(f.s, f.api, 1000));
    assert.equal(f.effects.length, 0);
  }
});
test("unknown stack delete is durable and not replayed when no effect is visible", async () => {
  const f = fixture();
  f.api.deleteStack = async () => {
    throw Error("response lost");
  };
  await assert.rejects(run(f.s, f.api, 1000));
  assert.ok(f.record.deleteIntent);
  assert.equal(
    (await run(f.s, f.api, 1001)).reason,
    "prior_delete_outcome_unknown",
  );
});
test("a lost control write does not authorize a destructive request", async () => {
  const f = fixture();
  f.api.writeControl = async () => {
    throw Error("unknown");
  };
  await assert.rejects(run(f.s, f.api, 1000));
  assert.equal(f.effects.length, 0);
});
test("stack replacement and unbound absence never authorize orphan cleanup", async () => {
  const f = fixture();
  await run(f.s, f.api, 999);
  f.stack = { ...f.stack, StackId: f.stack.StackId + "-replacement" };
  await assert.rejects(run(f.s, f.api, 1000));
  assert.equal(f.effects.length, 0);
  const empty = fixture();
  empty.stack = null;
  assert.equal(
    (await run(empty.s, empty.api, 1000)).status,
    "unbound_stack_absent",
  );
});
test("only owned available volumes are deleted after stack absence; unknown outcomes stop retries", async () => {
  const f = fixture();
  await run(f.s, f.api, 999);
  f.stack = null;
  f.data.volumes = [
    { VolumeId: "vol-012345", State: "available", Tags: f.data.tags },
  ];
  assert.equal((await run(f.s, f.api, 1000)).destructiveActions, 1);
  assert.equal(
    (await run(f.s, f.api, 1001)).reason,
    "prior_volume_delete_unknown",
  );
  const g = fixture();
  await run(g.s, g.api, 999);
  g.stack = null;
  g.data.volumes = [{ VolumeId: "vol-012345", State: "available", Tags: [] }];
  await assert.rejects(run(g.s, g.api, 1000));
  assert.equal(g.effects.length, 0);
});
test("partial object cleanup failure never proceeds to stack deletion", async () => {
  const f = fixture();
  f.api.deleteObjects = async () => {
    throw Error("partially deleted");
  };
  await assert.rejects(run(f.s, f.api, 1000));
  assert.equal(f.record.deleteIntent, null);
  assert.equal(f.effects.length, 0);
});
test("invalid clocks and incomplete SDK inventories never authorize cleanup", async () => {
  const f = fixture();
  await assert.rejects(run(f.s, f.api, NaN));
  const calls = [];
  let response = { Contents: [], IsTruncated: true };
  function library(clientName) {
    return new Proxy(
      {
        [clientName]: class {
          async send(command) {
            calls.push(command);
            return response;
          }
        },
      },
      {
        get(t, k) {
          if (k in t) return t[k];
          return class {
            constructor(input) {
              this.type = k;
              this.input = input;
            }
          };
        },
      },
    );
  }
  const api = require("./index.cjs").provider(f.s, {
    cf: library("CloudFormationClient"),
    s3: library("S3Client"),
    ec: library("EC2Client"),
    ddb: library("DynamoDBClient"),
  });
  await assert.rejects(api.objects(), /incomplete_inventory/);
  assert.equal(calls[0].input.ExpectedBucketOwner, f.s.account);
  response = { KeyCount: 0, IsTruncated: false };
  assert.deepEqual(await api.objects(), []);
  response = {};
  await assert.rejects(api.objects(), /invalid_inventory/);
  await assert.rejects(api.volumes(), /invalid_inventory/);
  response = {
    Contents: [],
    IsTruncated: true,
    NextContinuationToken: "repeat",
  };
  await assert.rejects(api.objects(), /repeated_token/);
});
