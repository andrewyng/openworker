// Cloud-side expiry guard. No caller-supplied resource names or scope.
const {
  CloudFormationClient,
  DescribeStacksCommand,
  DeleteStackCommand,
} = require("@aws-sdk/client-cloudformation");
const {
  S3Client,
  ListObjectsV2Command,
  DeleteObjectsCommand,
  AbortMultipartUploadCommand,
  ListMultipartUploadsCommand,
} = require("@aws-sdk/client-s3");
const {
  EC2Client,
  DescribeVolumesCommand,
  DeleteVolumeCommand,
} = require("@aws-sdk/client-ec2");
const {
  EventBridgeClient,
  DisableRuleCommand,
} = require("@aws-sdk/client-eventbridge");
const cfn = new CloudFormationClient({}),
  s3 = new S3Client({}),
  ec2 = new EC2Client({});
exports.handler = async (event, context) => {
  const { EXPERIMENT, ACCOUNT, EXPIRY, BUCKET } = process.env;
  if (
    context.invokedFunctionArn.split(":")[4] !== ACCOUNT ||
    !/^ow-taskdesk-\d{8}$/.test(EXPERIMENT) ||
    BUCKET !== `${EXPERIMENT}-${ACCOUNT}`
  )
    throw Error("Cleanup identity mismatch");
  const expiry = Date.parse(EXPIRY);
  if (!Number.isFinite(expiry)) throw Error("Invalid expiry");
  if (Date.now() < expiry) return { status: "not_due", expiry: EXPIRY };
  let stack;
  try {
    stack = (
      await cfn.send(new DescribeStacksCommand({ StackName: EXPERIMENT }))
    ).Stacks[0];
  } catch (e) {
    if (
      e.name !== "ValidationError" ||
      !String(e.message).includes("does not exist")
    )
      throw e;
  }
  if (
    stack &&
    !stack.Tags?.some((t) => t.Key === "Project" && t.Value === EXPERIMENT)
  )
    throw Error("Refusing unowned stack");
  if (stack) {
    // No versioned bucket in this experiment. Each pass is bounded; scheduled
    // retries finish larger buckets and stack dependency cleanup after node exit.
    try {
      for (let n = 0; n < 5; n++) {
        const page = await s3.send(
          new ListObjectsV2Command({ Bucket: BUCKET, MaxKeys: 1000 }),
        );
        if (!page.Contents?.length) break;
        const deleted = await s3.send(
          new DeleteObjectsCommand({
            Bucket: BUCKET,
            Delete: {
              Objects: page.Contents.map((o) => ({ Key: o.Key })),
              Quiet: true,
            },
          }),
        );
        if (deleted.Errors?.length) throw Error("Artifact cleanup incomplete");
      }
      const uploads = await s3.send(
        new ListMultipartUploadsCommand({ Bucket: BUCKET, MaxUploads: 100 }),
      );
      for (const upload of uploads.Uploads || [])
        await s3.send(
          new AbortMultipartUploadCommand({
            Bucket: BUCKET,
            Key: upload.Key,
            UploadId: upload.UploadId,
          }),
        );
    } catch (e) {
      if (e.name !== "NoSuchBucket") throw e;
    }
    if (stack.StackStatus !== "DELETE_IN_PROGRESS")
      await cfn.send(new DeleteStackCommand({ StackName: stack.StackId }));
  }
  // CSI-created data volumes aren't CloudFormation resources. The dedicated
  // storage class tags them; delete only detached volumes with our exact tag.
  const volumes = await ec2.send(
    new DescribeVolumesCommand({
      Filters: [{ Name: "tag:Project", Values: [EXPERIMENT] }],
    }),
  );
  for (const volume of volumes.Volumes || [])
    if (volume.State === "available")
      await ec2.send(new DeleteVolumeCommand({ VolumeId: volume.VolumeId }));
  if (!stack && !volumes.Volumes?.length)
    await new EventBridgeClient({}).send(
      new DisableRuleCommand({ Name: EXPERIMENT + "-cleanup" }),
    );
  console.log(
    JSON.stringify({
      experiment: EXPERIMENT,
      stack: stack?.StackStatus || "absent",
      remainingOwnedVolumes: volumes.Volumes?.length || 0,
    }),
  );
  return { status: stack ? "cleanup_requested" : "stack_absent" };
};
