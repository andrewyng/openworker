import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { connect, account, region } from "./client.mjs";
import { name } from "./templates.mjs";
import { run, repo } from "../helm/local.mjs";
const { aws } = await connect(),
  registry = `${account}.dkr.ecr.${region}.amazonaws.com`;
const token = (
  await aws(["ecr", "get-authorization-token", "--registry-ids", account])
).authorizationData[0];
if (token.proxyEndpoint !== "https://" + registry)
  throw Error("Unexpected registry");
const password = Buffer.from(token.authorizationToken, "base64")
  .toString()
  .slice(4);
const directory = await mkdtemp(join(tmpdir(), "ow-taskdesk-docker-"));
const host = (
  await run("docker", [
    "context",
    "inspect",
    "--format",
    "{{.Endpoints.docker.Host}}",
  ])
).trim();
const docker = (args) =>
  run("docker", ["--config", directory, "--host", host, ...args], {
    timeout: 600000,
  });
try {
  await run(
    "docker",
    [
      "--config",
      directory,
      "--host",
      host,
      "login",
      "--username",
      "AWS",
      "--password-stdin",
      registry,
    ],
    { input: password },
  );
  const images = {};
  for (const [role, local] of [
    ["harness", process.argv[3] || "openworker-harness:taskdesk-20261012"],
    ["runner", process.argv[4] || "openworker-runner:taskdesk-20261012"],
    ["taskdesk", process.argv[5] || "openworker-taskdesk:20261012"],
  ]) {
    if (!/^[a-zA-Z0-9./_-]+:[a-zA-Z0-9._-]+$/.test(local))
      throw Error("Expected explicit trusted local image tag");
    const [meta] = JSON.parse(await docker(["image", "inspect", local]));
    if (meta.Os !== "linux" || meta.Architecture !== "arm64")
      throw Error("This experiment requires Linux ARM64 images");
    const repository = name + "/" + role,
      tag = process.argv[2] || "qualification-v1";
    if (!/^[a-z0-9-]{1,64}$/.test(tag))
      throw Error("Invalid immutable image tag");
    await aws([
      "ecr",
      "describe-repositories",
      "--repository-names",
      repository,
    ]);
    await docker(["tag", local, `${registry}/${repository}:${tag}`]);
    await docker(["push", `${registry}/${repository}:${tag}`]);
    const remote = await aws([
      "ecr",
      "describe-images",
      "--repository-name",
      repository,
      "--image-ids",
      "imageTag=" + tag,
    ]);
    images[role] =
      `${registry}/${repository}@${remote.imageDetails[0].imageDigest}`;
    console.log("Published " + role + " by immutable digest");
  }
  await writeFile(
    join(repo, ".ow-aws/images.json"),
    JSON.stringify(images, null, 2),
    { mode: 0o600 },
  );
} finally {
  await rm(directory, { recursive: true });
}
