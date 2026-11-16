// Explicit experiment-only promotion after deterministic acceptance.
// Supply the previously authorized OpenAI key on stdin, never argv or a file.
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { repo, kubectl, run } from "../helm/local.mjs";
import { name } from "./templates.mjs";
const root = join(repo, ".ow-aws"),
  m = JSON.parse(await readFile(join(root, "kubernetes.json"), "utf8"));
const plan = JSON.parse(await readFile(join(root, "plan.json"), "utf8"));
if (m.context !== name || Date.now() > Date.parse(plan.expiry))
  throw Error("Wrong or expired experiment");
const proof = JSON.parse(
  await readFile(join(root, "taskdesk-test/qualification.json"), "utf8"),
);
if (proof.checks.length < 8)
  throw Error("Complete deterministic cloud acceptance first");
let key = "";
for await (const chunk of process.stdin) {
  key += chunk;
  if (key.length > 4096) throw Error("Invalid key input");
}
key = key.trim();
if (!key || /\s/.test(key))
  throw Error("Key must be supplied privately on stdin");
const model = "gpt-6-sol";
const check = await fetch("https://api.openai.com/v1/models/" + model, {
  headers: { Authorization: "Bearer " + key },
  redirect: "error",
  signal: AbortSignal.timeout(20000),
});
if (!check.ok)
  throw Error("Configured Sol model is unavailable: HTTP " + check.status);
await check.body?.cancel();
await kubectl(
  m,
  ["apply", "--server-side", "--field-manager=ow-experiment", "-f", "-"],
  JSON.stringify({
    apiVersion: "v1",
    kind: "Secret",
    metadata: { name: "openai-model", namespace: "ow-app" },
    type: "Opaque",
    data: { OPENAI_API_KEY: Buffer.from(key).toString("base64") },
  }),
);
key = "";
const values = JSON.parse(await readFile(join(root, "values.json"), "utf8"));
values.config.model = model;
values.config.scripted = false;
values.secrets.model = "openai-model";
values.taskDesk.config.mode = "live";
await writeFile(join(root, "values.json"), JSON.stringify(values, null, 2), {
  mode: 0o600,
});
await run(m.helm, [
  "upgrade",
  "ow",
  join(repo, "deploy/helm/openworker"),
  "--kubeconfig",
  m.kubeconfig,
  "--kube-context",
  m.context,
  "--namespace",
  "ow-app",
  "-f",
  join(root, "values.json"),
  "--wait",
  "--timeout",
  "180s",
]);
console.log(
  "Sol configured on workers only. No provider key written to local state, browser, sandbox, Helm values or command arguments.",
);
