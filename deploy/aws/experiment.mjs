import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { connect, account, region } from "./client.mjs";
import { infra, name } from "./templates.mjs";
import { guard } from "./guard.mjs";
const root = fileURLToPath(new URL("../../.ow-aws/", import.meta.url));
await mkdir(root, { recursive: true, mode: 0o700 });
const { aws } = await connect(),
  action = process.argv[2] || "status";
const path = join(root, "plan.json");
const stack = async (n) => {
  try {
    return (await aws(["cloudformation", "describe-stacks", "--stack-name", n]))
      .Stacks[0];
  } catch (e) {
    if (String(e.stderr).includes("does not exist")) return null;
    throw e;
  }
};
const assertOwned = (s) => {
  if (s && !s.Tags?.some((t) => t.Key === "Project" && t.Value === name))
    throw Error("Refusing unowned stack");
};
if (action === "plan") {
  let plan;
  try {
    plan = JSON.parse(await readFile(path, "utf8"));
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  if (!plan) {
    const operatorIp = (
      await (
        await fetch("https://checkip.amazonaws.com", {
          signal: AbortSignal.timeout(10000),
        })
      ).text()
    ).trim();
    const addon = async (name) => {
      const d = await aws([
        "eks",
        "describe-addon-versions",
        "--kubernetes-version",
        "1.36",
        "--addon-name",
        name,
      ]);
      const versions = d.addons[0]?.addonVersions || [];
      const v = versions.find(
        (v) =>
          v.architecture.includes("arm64") &&
          v.compatibilities.some((c) => c.defaultVersion),
      );
      if (!v) throw Error("No default ARM64 add-on " + name);
      return v.addonVersion;
    };
    const pg = await aws([
      "rds",
      "describe-db-engine-versions",
      "--engine",
      "postgres",
      "--engine-version",
      "16",
      "--query",
      "DBEngineVersions[].EngineVersion",
    ]);
    const pgVersion = pg
      .filter((v) => /^16\.\d+$/.test(v))
      .sort((a, b) => Number(b.split(".")[1]) - Number(a.split(".")[1]))[0];
    if (!pgVersion) throw Error("No PostgreSQL 16 version");
    plan = {
      account,
      region,
      name,
      created: new Date().toISOString(),
      expiry: new Date(Date.now() + 24 * 3600000).toISOString(),
      budget: 100,
      operatorCidr: operatorIp + "/32",
      cniVersion: await addon("vpc-cni"),
      podIdentityVersion: await addon("eks-pod-identity-agent"),
      ebsVersion: await addon("aws-ebs-csi-driver"),
      pgVersion,
    };
    await writeFile(path, JSON.stringify(plan, null, 2), {
      mode: 0o600,
      flag: "wx",
    });
  }
  const code = await readFile(new URL("cleanup.cjs", import.meta.url), "utf8");
  for (const [file, value] of [
    ["infra.json", infra(plan)],
    ["guard.json", guard({ ...plan, code })],
  ]) {
    const target = join(root, file);
    await writeFile(target, JSON.stringify(value, null, 2), { mode: 0o600 });
    await aws([
      "cloudformation",
      "validate-template",
      "--template-body",
      "file://" + target,
    ]);
  }
  console.log(
    JSON.stringify(
      {
        ...plan,
        operatorCidr: "[single current operator IPv4 /32]",
        validated: true,
      },
      null,
      2,
    ),
  );
} else if (["guard", "launch"].includes(action)) {
  const plan = JSON.parse(await readFile(path, "utf8"));
  if (Date.parse(plan.expiry) - Date.now() < 2 * 3600000)
    throw Error("Too little time before immutable experiment deadline");
  if (
    plan.account !== account ||
    plan.region !== region ||
    plan.budget !== 100 ||
    plan.name !== name
  )
    throw Error("Experiment scope mismatch");
  const target = action === "guard" ? name + "-guard" : name;
  const current = await stack(target);
  assertOwned(current);
  if (current) {
    console.log(JSON.stringify({ stack: target, status: current.StackStatus }));
    process.exit(0);
  }
  if (action === "launch") {
    const guardStack = await stack(name + "-guard");
    assertOwned(guardStack);
    if (guardStack?.StackStatus !== "CREATE_COMPLETE")
      throw Error("Cloud-side expiry guard must be ready first");
    const fn = await aws([
      "lambda",
      "get-function-configuration",
      "--function-name",
      name + "-cleanup",
    ]);
    if (
      fn.Environment?.Variables?.EXPIRY !== plan.expiry ||
      fn.State !== "Active"
    )
      throw Error("Cleanup expiry/function mismatch");
    const rule = await aws([
      "events",
      "describe-rule",
      "--name",
      name + "-cleanup",
    ]);
    if (rule.State !== "ENABLED") throw Error("Cleanup schedule not enabled");
  }
  const result = await aws([
    "cloudformation",
    "create-stack",
    "--stack-name",
    target,
    "--template-body",
    "file://" + join(root, action === "guard" ? "guard.json" : "infra.json"),
    "--capabilities",
    "CAPABILITY_NAMED_IAM",
    "--tags",
    `Key=Project,Value=${name}`,
    `Key=ExpiresAt,Value=${plan.expiry}`,
    "--on-failure",
    "ROLLBACK",
  ]);
  console.log(
    JSON.stringify({ stack: target, id: result.StackId, expiry: plan.expiry }),
  );
} else if (action === "status") {
  for (const n of [name + "-guard", name]) {
    const s = await stack(n);
    assertOwned(s);
    console.log(
      JSON.stringify({ stack: n, status: s?.StackStatus || "absent" }),
    );
    if (s) {
      const events = await aws([
        "cloudformation",
        "describe-stack-events",
        "--stack-name",
        n,
        "--query",
        "StackEvents[:8].{resource:LogicalResourceId,status:ResourceStatus,reason:ResourceStatusReason}",
      ]);
      console.log(JSON.stringify(events));
      if (s.Outputs)
        await writeFile(
          join(root, n + "-outputs.json"),
          JSON.stringify(s.Outputs, null, 2),
          { mode: 0o600 },
        );
    }
  }
} else throw Error("Use plan | guard | launch | status");
