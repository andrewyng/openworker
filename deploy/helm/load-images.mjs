// Explicit local kind image import. No registry push, default context or image pruning.
import { spawn } from "node:child_process";
import { once } from "node:events";
import { run } from "./local.mjs";
const [kind, cluster, app, runner] = process.argv.slice(2);
if (
  !kind ||
  !/^[-a-z0-9]+$/.test(cluster) ||
  ![app, runner].every((s) => s && /^[a-zA-Z0-9./_-]+:[a-zA-Z0-9._-]+$/.test(s))
)
  throw Error(
    "Usage: node load-images.mjs /path/to/kind CLUSTER APP:TAG RUNNER:TAG",
  );
const nodes = (await run(kind, ["get", "nodes", "--name", cluster]))
  .trim()
  .split("\n");
if (!nodes.length || nodes.some((n) => !n.startsWith(cluster + "-")))
  throw Error("Unexpected kind node inventory");
const refs = [];
let platform;
const canonical = (tag) =>
  !tag.includes("/")
    ? "docker.io/library/" + tag
    : /[.:]/.test(tag.split("/")[0]) || tag.startsWith("localhost/")
      ? tag
      : "docker.io/" + tag;
for (const tag of [app, runner]) {
  const [meta] = JSON.parse(await run("docker", ["image", "inspect", tag]));
  if (!/^sha256:[a-f0-9]{64}$/.test(meta.Id) || meta.Os !== "linux")
    throw Error("Expected Linux content-addressed image");
  const p = "linux/" + meta.Architecture;
  if (platform && platform !== p)
    throw Error("App and runner CPU architecture differ");
  platform = p;
}
for (const node of nodes) {
  const save = spawn("docker", ["image", "save", app, runner], {
    stdio: ["ignore", "pipe", "inherit"],
  });
  const load = spawn(
    "docker",
    [
      "exec",
      "-i",
      node,
      "ctr",
      "-n",
      "k8s.io",
      "images",
      "import",
      "--platform",
      platform,
      "--digests",
      "-",
    ],
    { stdio: ["pipe", "ignore", "inherit"] },
  );
  save.stdout.pipe(load.stdin);
  load.stdin.on("error", () => {});
  const codes = await Promise.all([once(save, "exit"), once(load, "exit")]);
  if (codes.some(([c]) => c !== 0)) throw Error("Image import failed");
  const inventory = (
    await run("docker", ["exec", node, "ctr", "-n", "k8s.io", "images", "list"])
  )
    .split("\n")
    .map((line) => line.trim().split(/\s+/));
  for (const [i, tag] of [app, runner].entries()) {
    const source = canonical(tag),
      digest = inventory.find((fields) => fields[0] === source)?.[2];
    if (!/^sha256:[a-f0-9]{64}$/.test(digest || ""))
      throw Error("Imported manifest digest unavailable");
    const target = source.slice(0, source.lastIndexOf(":")) + "@" + digest;
    if (refs[i] && refs[i] !== target)
      throw Error("Node image manifests differ");
    refs[i] = target;
    if (!inventory.some((fields) => fields[0] === target))
      await run("docker", [
        "exec",
        node,
        "ctr",
        "-n",
        "k8s.io",
        "images",
        "tag",
        source,
        target,
      ]);
    else if (inventory.find((fields) => fields[0] === target)[2] !== digest)
      throw Error("Existing digest reference conflicts");
  }
}
console.log(JSON.stringify({ image: refs[0], runnerImage: refs[1] }, null, 2));
