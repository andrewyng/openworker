// Explicit early teardown of the approved disposable experiment, using the same
// cloud-side guard as expiry. No wildcard account cleanup and no target override.
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { connect, account } from "./client.mjs";
import { name } from "./templates.mjs";
import { repo, kubectl } from "../helm/local.mjs";
const root = join(repo, ".ow-aws"),
  { aws } = await connect(),
  action = process.argv[2];
const stack = async (target) => {
  try {
    return (
      await aws(["cloudformation", "describe-stacks", "--stack-name", target])
    ).Stacks[0];
  } catch (e) {
    if (String(e.stderr).includes("does not exist")) return null;
    throw e;
  }
};
const own = (s) => {
  if (s && !s.Tags?.some((t) => t.Key === "Project" && t.Value === name))
    throw Error("Refusing unowned stack");
};
if (action === "begin" || action === "abort") {
  const main = await stack(name),
    guard = await stack(name + "-guard");
  own(main);
  own(guard);
  if (
    !main ||
    (guard?.StackStatus !== "UPDATE_COMPLETE" &&
      guard?.StackStatus !== "CREATE_COMPLETE")
  )
    throw Error("Expected live experiment and ready cleanup guard");
  // Failure must never trap paid resources behind a success-only gate.
  // abort is explicit, same exact scope/guard, and records no qualification claim.
  if (action === "begin") {
    const proof = JSON.parse(
      await readFile(join(root, "current-qualification.json"), "utf8"),
    );
    if (proof.status !== "completed")
      throw Error(
        "Save qualification result before normal teardown; use abort after failure",
      );
  }
  let workloadsStopped = false;
  try {
    const m = JSON.parse(await readFile(join(root, "kubernetes.json"), "utf8"));
    if (m.context !== name) throw Error("Wrong cluster");
    const ns = JSON.parse(
      await kubectl(m, ["get", "namespace", "ow-app", "-o", "json"]),
    );
    if (ns.metadata.labels?.["openworker.dev/experiment"] !== name)
      throw Error("Unowned namespace");
    await kubectl(m, [
      "-n",
      "ow-app",
      "scale",
      "deployment",
      "-l",
      "app.kubernetes.io/name=openworker,app.kubernetes.io/component=worker",
      "--replicas=0",
    ]);
    await kubectl(m, [
      "-n",
      "ow-app",
      "delete",
      "secret",
      "openai-model",
      "--ignore-not-found=true",
    ]);
    workloadsStopped = true;
  } catch (error) {
    if (action !== "abort") throw error;
    console.log(
      "Workload cleanup unavailable; owned infrastructure teardown will remove it. No successful qualification claimed.",
    );
  }
  const expiry = new Date(Date.now() - 1000).toISOString();
  await aws([
    "lambda",
    "update-function-configuration",
    "--function-name",
    name + "-cleanup",
    "--environment",
    JSON.stringify({
      Variables: {
        EXPERIMENT: name,
        ACCOUNT: account,
        EXPIRY: expiry,
        BUCKET: `${name}-${account}`,
      },
    }),
  ]);
  await writeFile(
    join(root, "teardown-start.json"),
    JSON.stringify(
      {
        started: new Date().toISOString(),
        mainStack: main.StackId,
        guardStack: guard.StackId,
        providerSecretRemoved: workloadsStopped,
        aborted: action === "abort",
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "Owned cloud-side cleanup deadline advanced to now. Invoke after the function update finishes; workload cleanup status is recorded privately.",
  );
} else if (action === "invoke") {
  const guard = await stack(name + "-guard");
  own(guard);
  if (!guard) throw Error("No cleanup guard");
  const config = await aws([
    "lambda",
    "get-function-configuration",
    "--function-name",
    name + "-cleanup",
  ]);
  if (
    config.LastUpdateStatus !== "Successful" ||
    Date.parse(config.Environment.Variables.EXPIRY) > Date.now()
  )
    throw Error("Guard update not ready or deadline not reached");
  const output = join(root, "cleanup-invocation.json");
  const result = await aws([
    "lambda",
    "invoke",
    "--function-name",
    name + "-cleanup",
    output,
  ]);
  if (result.FunctionError) {
    console.log(await readFile(output, "utf8"));
    throw Error("Cleanup invocation failed");
  }
  console.log(await readFile(output, "utf8"));
} else if (action === "verify") {
  const main = await stack(name);
  own(main);
  const volumes = await aws([
    "ec2",
    "describe-volumes",
    "--filters",
    `Name=tag:Project,Values=${name}`,
    "--query",
    "Volumes[].{id:VolumeId,state:State}",
  ]);
  const instances = await aws([
    "ec2",
    "describe-instances",
    "--filters",
    `Name=tag:Project,Values=${name}`,
    "--query",
    "Reservations[].Instances[].{id:InstanceId,state:State.Name}",
  ]);
  const result = {
    time: new Date().toISOString(),
    stack: main?.StackStatus || "absent",
    volumes,
    instances,
    complete:
      !main &&
      volumes.length === 0 &&
      instances.every((i) => i.state === "terminated"),
  };
  await writeFile(
    join(root, "teardown-verification.json"),
    JSON.stringify(result, null, 2),
    { mode: 0o600 },
  );
  console.log(JSON.stringify(result));
  if (main?.StackStatus === "DELETE_FAILED")
    console.log(
      JSON.stringify(
        await aws([
          "cloudformation",
          "describe-stack-events",
          "--stack-name",
          name,
          "--query",
          "StackEvents[?ResourceStatus==`DELETE_FAILED`].{resource:LogicalResourceId,reason:ResourceStatusReason}",
        ]),
      ),
    );
} else if (action === "remove-guard") {
  const main = await stack(name);
  if (main) throw Error("Main stack remains");
  const volumes = await aws([
    "ec2",
    "describe-volumes",
    "--filters",
    `Name=tag:Project,Values=${name}`,
  ]);
  if (volumes.Volumes.length) throw Error("Owned volumes remain");
  const guard = await stack(name + "-guard");
  own(guard);
  if (guard)
    await aws([
      "cloudformation",
      "delete-stack",
      "--stack-name",
      guard.StackId,
    ]);
  console.log(
    "Owned guard stack removal requested after main resource verification.",
  );
} else throw Error("Use begin | abort | invoke | verify | remove-guard");
