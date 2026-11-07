// Optional local live workspace, reusing this task's existing isolated Helm stack.
// Provider key arrives on stdin. Old session/history data is retained.
import https from "node:https";
import { readFile, writeFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { join } from "node:path";
import {
  settings,
  root,
  repo,
  namespace,
  kubectl,
  helm,
  run,
} from "../../deploy/helm/local.mjs";
const m = await settings();
if (m.context !== "kind-ow-server-20261011")
  throw Error("This helper is limited to the existing isolated test cluster");
let key = "";
for await (const c of process.stdin) {
  key += c;
  if (key.length > 4096) throw Error("Invalid key input");
}
key = key.trim();
if (!key || /\s/.test(key)) throw Error("Private provider key required");
const env = JSON.parse(await readFile(join(root, "environment.json"), "utf8"));
const dir = join(root, "task-desk"),
  tokenPath = join(dir, "live-operator");
let token;
try {
  token = (await readFile(tokenPath, "utf8")).trim();
} catch (e) {
  if (e.code !== "ENOENT") throw e;
  token = randomBytes(32).toString("hex");
  await writeFile(tokenPath, token, { mode: 0o600, flag: "wx" });
}
const admin = (await readFile(join(root, "private/admin"), "utf8")).trim(),
  ca = await readFile(join(root, "private/api.ca.crt"));
const body = JSON.stringify({
  request_id: "task-desk-live-local-v1",
  token,
  spec: {
    principal: "task-desk-live-local",
    role: "operator",
    scope: { kind: "owned" },
    environments: [env.id],
  },
});
await new Promise((resolve, reject) => {
  const q = https.request(
    {
      hostname: "localhost",
      family: 4,
      port: 9443,
      path: "/credentials",
      method: "POST",
      ca,
      headers: {
        Authorization: "Bearer " + admin,
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    },
    (r) => {
      r.resume();
      r.on("end", () =>
        r.statusCode === 200
          ? resolve()
          : reject(Error("Local app key setup failed")),
      );
    },
  );
  q.on("error", () => reject(Error("Local API unavailable for key setup")));
  q.end(body);
});
const imported = JSON.parse(
  await run(
    "node",
    [
      join(repo, "deploy/helm/load-images.mjs"),
      process.env.OW_KIND || "kind",
      m.context.slice(5),
      "openworker-harness:taskdesk-20261011",
      "openworker-runner:helm-20261011",
    ],
    { timeout: 240000 },
  ),
);
await kubectl(
  m,
  ["apply", "--server-side", "--field-manager=taskdesk-local", "-f", "-"],
  JSON.stringify({
    apiVersion: "v1",
    kind: "Secret",
    metadata: { name: "taskdesk-model", namespace },
    type: "Opaque",
    data: { OPENAI_API_KEY: Buffer.from(key).toString("base64") },
  }),
);
key = "";
const values = JSON.parse(await readFile(join(root, "values.json"), "utf8"));
values.image = imported.image;
values.config.model = "gpt-6-sol";
values.config.scripted = false;
values.secrets.model = "taskdesk-model";
values.network.extraEgress = [
  {
    to: [
      {
        ipBlock: {
          cidr: "0.0.0.0/0",
          except: [
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "169.254.0.0/16",
          ],
        },
      },
    ],
    ports: [{ protocol: "TCP", port: 443 }],
  },
];
await writeFile(join(root, "values.json"), JSON.stringify(values, null, 2), {
  mode: 0o600,
});
await helm(m, ["--wait", "--timeout", "180s"]);
await writeFile(
  join(dir, "live-config.json"),
  JSON.stringify(
    {
      apis: ["https://localhost:9443"],
      api_token_file: tokenPath,
      access_key_file: join(dir, "access"),
      environment_id: env.id,
      coworker: "task-desk",
      workspace: "/sandbox",
      port: 54350,
      mode: "live",
    },
    null,
    2,
  ),
  { mode: 0o600 },
);
console.log(
  "Local live workspace ready with a separate owned principal. Reconnect the API tunnel and start Task Desk using .ow-helm/task-desk/live-config.json.",
);
