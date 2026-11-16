// Provision only the app's credential in the existing dedicated local Helm fixture.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import https from "node:https";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
const root = fileURLToPath(new URL("../../.ow-helm/", import.meta.url));
const dir = join(root, "task-desk");
await mkdir(dir, { recursive: true, mode: 0o700 });
const api = "https://localhost:9443";
const ca = await readFile(join(root, "private/api.ca.crt"));
const admin = (await readFile(join(root, "private/admin"), "utf8")).trim();
const environment = JSON.parse(
  await readFile(join(root, "environment.json"), "utf8"),
);
async function secret(name) {
  const path = join(dir, name);
  try {
    return (await readFile(path, "utf8")).trim();
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
    const value = randomBytes(32).toString("hex");
    await writeFile(path, value, { mode: 0o600, flag: "wx" });
    return value;
  }
}
const token = await secret("operator");
await secret("access");
const payload = JSON.stringify({
  request_id: "task-desk-local-v1",
  token,
  spec: {
    principal: "task-desk-local",
    role: "operator",
    scope: { kind: "owned" },
    environments: [environment.id],
  },
});
await new Promise((resolve, reject) => {
  const req = https.request(
    api + "/credentials",
    {
      method: "POST",
      ca,
      family: 4,
      headers: {
        Authorization: "Bearer " + admin,
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(payload),
      },
    },
    (r) => {
      r.resume();
      r.on("end", () =>
        r.statusCode === 200
          ? resolve()
          : reject(Error("Credential setup failed " + r.statusCode)),
      );
    },
  );
  req.on("error", reject);
  req.end(payload);
});
await writeFile(
  join(dir, "config.json"),
  JSON.stringify(
    {
      apis: [api],
      api_token_file: join(dir, "operator"),
      access_key_file: join(dir, "access"),
      environment_id: environment.id,
      coworker: "assistant-example",
      workspace: "/sandbox",
      port: 54350,
      mode: "scripted-test",
    },
    null,
    2,
  ),
  { mode: 0o600 },
);
console.log(
  "Task Desk local configuration ready. Access key is in .ow-helm/task-desk/access; never commit it.",
);
