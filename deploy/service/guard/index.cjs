// Scheduled experiment cleanup. Scope comes only from immutable environment,
// never event input. Provider methods are injected for local fault qualification.
"use strict";
function settings(env, context) {
  const {
    OWS_DEPLOYMENT_ID: id,
    OWS_ACCOUNT: account,
    OWS_REGION: region,
    OWS_STACK_NAME: stack,
    OWS_BUCKET: bucket,
    OWS_OBJECT_PREFIX: objectPrefix,
    OWS_EXECUTION_ROLE: role,
  } = env;
  if (
    !/^ows-[a-f0-9]{32}$/.test(id || "") ||
    !/^\d{12}$/.test(account || "") ||
    !/^[a-z]{2}-[a-z]+-\d+$/.test(region || "")
  )
    throw Error("invalid_scope");
  const prefix = stack?.replace(/-infra$/, "");
  if (
    !new RegExp(
      "^ows-[a-z][a-z0-9-]{1,28}[a-z0-9]-" + id.slice(4, 16) + "$",
    ).test(prefix || "") ||
    stack !== prefix + "-infra" ||
    bucket !== prefix + "-" + account ||
    objectPrefix !== id + "/" ||
    role !== `arn:aws:iam::${account}:role/${prefix}-executor`
  )
    throw Error("invalid_scope");
  if (
    context?.invokedFunctionArn !==
    `arn:aws:lambda:${region}:${account}:function:${prefix}-cleanup`
  )
    throw Error("invalid_function_identity");
  const expiry = Number(env.OWS_EXPIRY);
  if (!Number.isSafeInteger(expiry) || expiry <= 0)
    throw Error("invalid_expiry");
  return {
    id,
    account,
    region,
    stack,
    bucket,
    objectPrefix,
    role,
    prefix,
    expiry,
    table: prefix + "-control",
  };
}
function tagged(tags, id) {
  return (
    Array.isArray(tags) &&
    tags.some((t) => t.Key === "ows:deployment-id" && t.Value === id)
  );
}
async function run(s, api, now, event = {}) {
  if (!Number.isSafeInteger(now) || now < 0) throw Error("invalid_clock");
  if (event.mode && !["health", "cleanup"].includes(event.mode))
    throw Error("invalid_mode");
  let record = await api.readControl();
  if (
    record &&
    (record.id !== s.id ||
      record.expiry !== s.expiry ||
      !Number.isSafeInteger(record.version) ||
      record.version < 1 ||
      typeof record.stackArn !== "string")
  )
    throw Error("control_identity_mismatch");
  const stack = await api.stack();
  if (stack) {
    const arnPrefix = `arn:aws:cloudformation:${s.region}:${s.account}:stack/${s.stack}/`;
    if (
      stack.StackName !== s.stack ||
      !stack.StackId?.startsWith(arnPrefix) ||
      !/^[a-zA-Z0-9-]+$/.test(stack.StackId.slice(arnPrefix.length)) ||
      !tagged(stack.Tags, s.id)
    )
      throw Error("stack_ownership_mismatch");
    if (record && record.stackArn !== stack.StackId)
      throw Error("stack_replaced");
  }
  if (event.mode === "health")
    return {
      status: "healthy",
      identityBound: !!record,
      destructiveActions: 0,
    };
  // Before the first destructive request, bind the observed immutable ARN in a
  // guard-owned CAS record. A lost control write cannot authorize deletion.
  if (stack && !record) {
    record = {
      id: s.id,
      expiry: s.expiry,
      stackArn: stack.StackId,
      version: 1,
      volumes: {},
      deleteIntent: null,
    };
    await api.writeControl(record, null);
  }
  async function checkpoint() {
    const previous = record.version;
    record.version++;
    await api.writeControl(record, previous);
  }
  if (now < s.expiry) return { status: "not_due", destructiveActions: 0 };
  if (stack) {
    if (stack.StackStatus === "DELETE_IN_PROGRESS")
      return { status: "deleting", destructiveActions: 0 };
    if (stack.StackStatus === "DELETE_FAILED")
      return {
        status: "operator_attention",
        reason: "stack_delete_failed",
        destructiveActions: 0,
      };
    if (
      ![
        "CREATE_COMPLETE",
        "UPDATE_COMPLETE",
        "UPDATE_ROLLBACK_COMPLETE",
        "ROLLBACK_COMPLETE",
        "CREATE_FAILED",
        "UPDATE_FAILED",
      ].includes(stack.StackStatus)
    )
      return { status: "waiting_for_stack", destructiveActions: 0 };
    if (record.deleteIntent)
      return {
        status: "operator_attention",
        reason: "prior_delete_outcome_unknown",
        destructiveActions: 0,
      };
    const info = await api.bucket();
    let objects = [],
      uploads = [];
    if (info) {
      if (!tagged(info.tags, s.id) || info.versioning)
        throw Error("bucket_ownership_or_versioning");
      // Inventory is deliberately bounded and complete BEFORE deletion. Refuse
      // overflow/foreign content rather than deleting a shared or partial scope.
      objects = await api.objects();
      uploads = await api.uploads();
      if (
        objects.length > 2000 ||
        uploads.length > 200 ||
        [...objects, ...uploads].some(
          (o) => typeof o.Key !== "string" || !o.Key.startsWith(s.objectPrefix),
        )
      )
        throw Error("foreign_or_oversized_bucket_inventory");
    }
    for (let n = 0; n < objects.length; n += 1000)
      await api.deleteObjects(objects.slice(n, n + 1000).map((o) => o.Key));
    for (const upload of uploads) await api.abortUpload(upload);
    record.deleteIntent = { token: s.id + "-expiry", at: now };
    await checkpoint();
    // Unknown outcome is not retried. A later invocation reads original status.
    await api.deleteStack(record.stackArn, record.deleteIntent.token);
    return { status: "cleanup_requested", destructiveActions: 1 };
  }
  // Stack absence without a durable original binding is not enough evidence to
  // destroy orphan resources. Let an operator inspect incomplete bootstrap.
  if (!record) return { status: "unbound_stack_absent", destructiveActions: 0 };
  const volumes = await api.volumes();
  if (volumes.length > 100) throw Error("volume_inventory_limit");
  let deleted = 0,
    pending = 0;
  for (const v of volumes) {
    if (!/^vol-[a-f0-9]+$/.test(v.VolumeId || "") || !tagged(v.Tags, s.id))
      throw Error("volume_ownership_mismatch");
    if (v.State !== "available") {
      pending++;
      continue;
    }
    if (record.volumes[v.VolumeId])
      return {
        status: "operator_attention",
        reason: "prior_volume_delete_unknown",
        destructiveActions: deleted,
      };
    record.volumes[v.VolumeId] = true;
    await checkpoint();
    await api.deleteVolume(v.VolumeId);
    deleted++;
  }
  return {
    status: volumes.length ? "volumes_pending" : "teardown_observed_complete",
    pending,
    destructiveActions: deleted,
  };
}
function provider(s, libraries) {
  const cf = libraries?.cf ?? require("@aws-sdk/client-cloudformation"),
    S3 = libraries?.s3 ?? require("@aws-sdk/client-s3"),
    ec = libraries?.ec ?? require("@aws-sdk/client-ec2"),
    D = libraries?.ddb ?? require("@aws-sdk/client-dynamodb");
  const options = { region: s.region, maxAttempts: 1 },
    cfn = new cf.CloudFormationClient(options),
    s3 = new S3.S3Client(options),
    ec2 = new ec.EC2Client(options),
    ddb = new D.DynamoDBClient(options);
  const key = { deploymentId: { S: s.id } };
  async function pages(send, field, tokenName, maxRows) {
    const rows = [];
    let token;
    const seen = new Set();
    for (let n = 0; n < 20; n++) {
      const r = await send(token);
      const entries =
        r[field] ??
        (field === "Contents" && r.KeyCount === 0 && !r.IsTruncated
          ? []
          : undefined);
      if (!Array.isArray(entries)) throw Error("invalid_inventory");
      rows.push(...entries);
      if (rows.length > maxRows) throw Error("inventory_limit");
      token = r[tokenName];
      if (field === "Contents" && r.IsTruncated && !token)
        throw Error("incomplete_inventory");
      if (!token) return rows;
      if (typeof token !== "string" || token.length > 8192)
        throw Error("invalid_token");
      if (seen.has(token)) throw Error("repeated_token");
      seen.add(token);
    }
    throw Error("page_limit");
  }
  return {
    async readControl() {
      const r = await ddb.send(
        new D.GetItemCommand({
          TableName: s.table,
          Key: key,
          ConsistentRead: true,
        }),
      );
      return r.Item ? JSON.parse(r.Item.body.S) : null;
    },
    async writeControl(record, expected) {
      await ddb.send(
        new D.PutItemCommand({
          TableName: s.table,
          Item: {
            ...key,
            version: { N: String(record.version) },
            body: { S: JSON.stringify(record) },
          },
          ConditionExpression:
            expected === null
              ? "attribute_not_exists(deploymentId)"
              : "#v = :expected",
          ...(expected === null
            ? {}
            : {
                ExpressionAttributeNames: { "#v": "version" },
                ExpressionAttributeValues: {
                  ":expected": { N: String(expected) },
                },
              }),
        }),
      );
    },
    async stack() {
      try {
        const r = await cfn.send(
          new cf.DescribeStacksCommand({ StackName: s.stack }),
        );
        if (r.Stacks?.length !== 1) throw Error("invalid_stack_response");
        return r.Stacks[0];
      } catch (e) {
        if (
          e.name === "ValidationError" &&
          e.message === `Stack with id ${s.stack} does not exist`
        )
          return null;
        throw e;
      }
    },
    async bucket() {
      try {
        const tags = await s3.send(
          new S3.GetBucketTaggingCommand({
            Bucket: s.bucket,
            ExpectedBucketOwner: s.account,
          }),
        );
        const v = await s3.send(
          new S3.GetBucketVersioningCommand({
            Bucket: s.bucket,
            ExpectedBucketOwner: s.account,
          }),
        );
        return { tags: tags.TagSet, versioning: v.Status };
      } catch (e) {
        if (e.name === "NoSuchBucket") return null;
        throw e;
      }
    },
    objects: () =>
      pages(
        (t) =>
          s3.send(
            new S3.ListObjectsV2Command({
              Bucket: s.bucket,
              ExpectedBucketOwner: s.account,
              MaxKeys: 500,
              ...(t ? { ContinuationToken: t } : {}),
            }),
          ),
        "Contents",
        "NextContinuationToken",
        2000,
      ),
    async uploads() {
      const rows = [];
      let keyMarker, uploadMarker;
      const seen = new Set();
      for (let n = 0; n < 20; n++) {
        const r = await s3.send(
          new S3.ListMultipartUploadsCommand({
            Bucket: s.bucket,
            ExpectedBucketOwner: s.account,
            MaxUploads: 100,
            KeyMarker: keyMarker,
            UploadIdMarker: uploadMarker,
          }),
        );
        if (r.Uploads !== undefined && !Array.isArray(r.Uploads))
          throw Error("invalid_upload_inventory");
        rows.push(...(r.Uploads || []));
        if (rows.length > 200) throw Error("upload_limit");
        if (!r.IsTruncated) return rows;
        keyMarker = r.NextKeyMarker;
        uploadMarker = r.NextUploadIdMarker;
        const id = JSON.stringify([keyMarker, uploadMarker]);
        if (!keyMarker || !uploadMarker || seen.has(id))
          throw Error("upload_pagination");
        seen.add(id);
      }
      throw Error("upload_page_limit");
    },
    async deleteObjects(keys) {
      const r = await s3.send(
        new S3.DeleteObjectsCommand({
          Bucket: s.bucket,
          ExpectedBucketOwner: s.account,
          Delete: { Objects: keys.map((Key) => ({ Key })), Quiet: true },
        }),
      );
      if (r.Errors?.length) throw Error("object_cleanup_incomplete");
    },
    abortUpload: (u) =>
      s3.send(
        new S3.AbortMultipartUploadCommand({
          Bucket: s.bucket,
          ExpectedBucketOwner: s.account,
          Key: u.Key,
          UploadId: u.UploadId,
        }),
      ),
    deleteStack: (StackName, ClientRequestToken) =>
      cfn.send(
        new cf.DeleteStackCommand({
          StackName,
          ClientRequestToken,
          RoleARN: s.role,
        }),
      ),
    volumes: () =>
      pages(
        (t) =>
          ec2.send(
            new ec.DescribeVolumesCommand({
              Filters: [{ Name: "tag:ows:deployment-id", Values: [s.id] }],
              ...(t ? { NextToken: t } : {}),
            }),
          ),
        "Volumes",
        "NextToken",
        100,
      ),
    deleteVolume: (VolumeId) =>
      ec2.send(new ec.DeleteVolumeCommand({ VolumeId })),
  };
}
exports.settings = settings;
exports.run = run;
exports.provider = provider;
exports.handler = async (event, context) => {
  try {
    const s = settings(process.env, context);
    const result = await run(
      s,
      provider(s),
      Math.floor(Date.now() / 1000),
      event,
    );
    console.log(JSON.stringify({ deploymentId: s.id, ...result }));
    return result;
  } catch {
    throw Error(
      "OpenWorker cleanup incomplete; inspect the bound deployment and guard control record",
    );
  }
};
