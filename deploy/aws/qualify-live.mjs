// Optional single live task in a separate pinned cohort. Key arrives on stdin.
// Existing fixture/variant pools and their saved sessions are never relabelled.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID, createHash } from "node:crypto";
import { repo, run, kubectl, apply } from "../helm/local.mjs";
import {
  root,
  context,
  save,
  wait,
  pods,
  forward,
  client,
  permitPodIdentity,
} from "./qualification-lib.mjs";
const m = await context(),
  k = (args) => kubectl(m, ["-n", "ow-app", ...args]);
const proof = JSON.parse(
  await readFile(join(root, "current-http-qualification.json"), "utf8"),
);
assert.equal(proof.status, "completed");
let key = "";
for await (const b of process.stdin) {
  key += b;
  if (key.length > 4096) throw Error("Key input limit");
}
key = key.trim();
assert.ok(key && !/\s/.test(key));
const model = "gpt-6-sol";
const check = await fetch("https://api.openai.com/v1/models/" + model, {
  headers: { Authorization: "Bearer " + key },
  redirect: "error",
  signal: AbortSignal.timeout(20000),
});
assert.equal(check.status, 200, "Selected model unavailable");
await check.body?.cancel();
await apply(m, [
  {
    apiVersion: "v1",
    kind: "Secret",
    metadata: {
      name: "openai-model",
      namespace: "ow-app",
      labels: { "openworker.dev/experiment": m.context },
    },
    stringData: { OPENAI_API_KEY: key },
  },
]);
key = "";
const v = JSON.parse(await readFile(join(root, "values.json"), "utf8"));
v.api.replicas = 1;
v.worker.replicas = 1;
v.taskDesk.enabled = false;
v.config.scripted = false;
v.config.model = model;
v.secrets.model = "openai-model";
await permitPodIdentity(m, "live");
await save("live-values.json", v);
await run(m.helm, [
  "upgrade",
  "--install",
  "live",
  join(repo, "deploy/helm/openworker"),
  "--kubeconfig",
  m.kubeconfig,
  "--kube-context",
  m.context,
  "-n",
  "ow-app",
  "-f",
  join(root, "live-values.json"),
]);
for (const role of ["api", "worker"])
  await k([
    "rollout",
    "status",
    "deployment/live-openworker-" + role,
    "--timeout=180s",
  ]);
const f = await forward(m, (await pods(m, "api", "live"))[0].metadata.name),
  a = await client(f.port);
let sid,
  finished = false;
try {
  const environment = JSON.parse(
      await readFile(join(root, "environment.json"), "utf8"),
    ),
    path = "packing-" + randomUUID() + ".md";
  sid = (
    await a.json("/sessions", {
      request_id: randomUUID(),
      parameters: {
        name: "Live cloud packing checklist",
        coworker: "task-desk",
        workspace: "/sandbox",
        environment_id: environment.id,
      },
    })
  ).session_id;
  await a.json(`/sessions/${sid}/turns`, {
    submission_id: randomUUID(),
    text: `Write exactly five practical Markdown checklist bullets for a weekend trip to ${path}. Use write_file then read_file to verify it. Do not use run_shell or any other tools. Include the checklist in your final answer.`,
  });
  const approved = new Set();
  const result = await wait(async () => {
    const s = await a.snapshot(sid);
    assert.ok(
      !["failed", "cancelled", "interrupted", "truncated"].includes(
        s.turn?.status,
      ),
    );
    for (const i of s.interactions) {
      if (approved.has(i.id)) continue;
      assert.equal(i.kind, "approval");
      assert.ok(
        ["write_file", "read_file"].includes(i.call.name),
        "Unexpected tool: not approved",
      );
      const args = JSON.parse(i.call.arguments);
      assert.equal(args.path, path);
      if (i.call.name === "write_file")
        assert.ok(
          typeof args.content === "string" && args.content.length < 4096,
        );
      assert.ok(approved.size < 2);
      await a.approve(sid, s, i.call.name);
      approved.add(i.id);
    }
    return s.turn?.status === "completed" && s;
  }, 180000);
  assert.equal(result.mode, "live");
  assert.equal(result.session.model, model);
  assert.equal(approved.size, 2);
  const events = await a.events(sid, result.cursor),
    tools = events.filter((e) => e.type === "tool_finished");
  assert.deepEqual(
    tools.map((e) => e.data.name),
    ["write_file", "read_file"],
  );
  for (const t of tools) assert.notEqual(t.data.result.isError, true);
  const bytes = Buffer.from(JSON.stringify(result.messages)),
    sha = createHash("sha256").update(bytes).digest("hex");
  const artifact = await a.json(`/sessions/${sid}/artifacts`, {
    request_id: randomUUID(),
    name: "live-checklist.json",
    turn_id: result.turn.id,
    content_type: "application/json",
    size: bytes.length,
    sha256: sha,
  });
  assert.equal(
    (
      await a.request(`/sessions/${sid}/artifacts/${artifact.id}/content`, {
        method: "PUT",
        raw: bytes,
      })
    ).status,
    200,
  );
  assert.deepEqual(
    (await a.request(`/sessions/${sid}/artifacts/${artifact.id}/content`))
      .bytes,
    bytes,
  );
  finished = true;
  await save("current-live-qualification.json", {
    status: "completed",
    time: new Date().toISOString(),
    session: sid,
    model,
    approvals: approved.size,
    artifact: artifact.id,
    sha256: sha,
    checks: [
      "Live model calls real sandbox write/read under two bounded exact approvals",
      "Final response published and downloaded through real S3",
      "Separate model cohort leaves existing fixture sessions and pools intact",
    ],
  });
  console.log(
    "PASS live Sol, two exact approvals, sandbox write/read and verified S3 output",
  );
} finally {
  if (sid && !finished) {
    const s = await a.snapshot(sid).catch(() => null);
    if (s?.turn)
      await a
        .json(`/sessions/${sid}/controls`, {
          request_id: randomUUID(),
          turn_id: s.turn.id,
          action: { kind: "cancel" },
        })
        .catch(() => {});
  }
  f.close();
}
