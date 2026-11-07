// Synthetic model, real EKS/RDS/OpenShell/S3 and production HTTP protocol.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID, createHash } from "node:crypto";
import { kubectl } from "../helm/local.mjs";
import {
  root,
  context,
  save,
  wait,
  pods,
  forward,
  client,
  admin,
  planTool,
} from "./qualification-lib.mjs";
const m = await context(),
  checks = [],
  owned = [];
const pass = (s) => {
  checks.push(s);
  console.log("PASS " + s);
};
let a, b, sid;
const environment = JSON.parse(
  await readFile(join(root, "environment.json"), "utf8"),
);
async function connectApis() {
  for (const f of owned.splice(0)) f.close();
  const ps = await wait(async () => {
    const p = await pods(m, "api");
    return p.length === 2 && p;
  });
  for (const p of ps) owned.push(await forward(m, p.metadata.name));
  a = await client(owned[0].port);
  b = await client(owned[1].port);
}
async function create(label) {
  const request = {
    request_id: randomUUID(),
    parameters: {
      name: label,
      coworker: "task-desk",
      workspace: "/sandbox",
      environment_id: environment.id,
    },
  };
  const r = await a.json("/sessions", request),
    again = await b.json("/sessions", request);
  assert.equal(r.session_id, again.session_id);
  return r.session_id;
}
async function turn(id, tool, args) {
  return a.json(`/sessions/${id}/turns`, {
    submission_id: randomUUID(),
    text: planTool(tool, args),
  });
}
async function finish(id, tool) {
  const s = await b.state(id, "waiting");
  await a.approve(id, s, tool);
  return b.state(id, "completed");
}
async function toolResult(id, name) {
  const s = await b.snapshot(id),
    events = await b.events(id, s.cursor);
  const e = events
    .filter((e) => e.type === "tool_finished" && e.data.name === name)
    .at(-1);
  assert.ok(e);
  return { s, events, result: e.data.result };
}
async function shellProof(id) {
  const r = await toolResult(id, "run_shell");
  assert.notEqual(r.result.isError, true);
  return { s: r.s, events: r.events, ...JSON.parse(r.result.content[0].text) };
}
try {
  await connectApis();
  sid = await create("AWS current harness qualification");
  const file = "current-" + randomUUID() + ".txt",
    content = "Real current OpenWorker EKS write";
  await turn(sid, "write_file", { path: file, content });
  const pending = await a.state(sid, "waiting");
  await kubectl(m, [
    "-n",
    "ow-app",
    "rollout",
    "restart",
    "deployment/ow-openworker-api",
    "deployment/ow-openworker-worker",
  ]);
  for (const role of ["api", "worker"])
    await kubectl(m, [
      "-n",
      "ow-app",
      "rollout",
      "status",
      "deployment/ow-openworker-" + role,
      "--timeout=180s",
    ]);
  await connectApis();
  assert.deepEqual((await b.snapshot(sid)).interactions, pending.interactions);
  await a.approve(sid, pending, "write_file");
  await b.state(sid, "completed");
  const replay = await toolResult(sid, "write_file");
  assert.equal(
    replay.events.filter(
      (e) => e.type === "tool_finished" && e.data.name === "write_file",
    ).length,
    1,
  );
  pass(
    "Same-ID creation, two API nodes, full API/worker replacement, exact approval identity and contiguous replay",
  );
  await turn(sid, "read_file", { path: file });
  await finish(sid, "read_file");
  assert.ok(
    JSON.stringify((await toolResult(sid, "read_file")).result).includes(
      content,
    ),
  );
  pass("Real sandbox write and separately approved read-back");
  // Publish selected bytes through the actual artifact API and AWS Pod Identity.
  const current = await b.snapshot(sid),
    bytes = Buffer.from("Verified EKS qualification artifact.\n"),
    sha = createHash("sha256").update(bytes).digest("hex");
  const artifact = await a.json(`/sessions/${sid}/artifacts`, {
    request_id: randomUUID(),
    name: "proof.md",
    turn_id: current.turn.id,
    content_type: "text/markdown",
    size: bytes.length,
    sha256: sha,
  });
  // Dedicated bounded raw upload; client wrapper supports bytes explicitly.
  const uploaded = await a.request(
    `/sessions/${sid}/artifacts/${artifact.id}/content`,
    { method: "PUT", raw: bytes },
  );
  assert.equal(uploaded.status, 200);
  const downloaded = await b.request(
    `/sessions/${sid}/artifacts/${artifact.id}/content`,
  );
  assert.equal(downloaded.status, 200);
  assert.deepEqual(downloaded.bytes, bytes);
  pass(
    "Immutable S3 artifact publication and cross-API checksum-identical download via Pod Identity",
  );
  await turn(sid, "ask_user", {
    question: "Which format?",
    options: ["Checklist", "Paragraph"],
  });
  const question = await a.state(sid, "waiting"),
    i = question.interactions.find((i) => i.kind === "question");
  assert.ok(i);
  await b.json(`/sessions/${sid}/controls`, {
    request_id: randomUUID(),
    turn_id: i.turn_id,
    action: {
      kind: "response",
      interaction_id: i.id,
      response: "Checklist",
      binding: Object.fromEntries(
        [
          "bundle_digest",
          "schema_digest",
          "policy_digest",
          "resource_policy_digest",
        ].map((k) => [k, i[k]]),
      ),
    },
  });
  await a.state(sid, "completed");
  await turn(sid, "write_file", {
    path: "must-not-write-" + randomUUID(),
    content: "cancelled",
  });
  const cancel = await b.state(sid, "waiting");
  await a.json(`/sessions/${sid}/controls`, {
    request_id: randomUUID(),
    turn_id: cancel.turn.id,
    action: { kind: "cancel" },
  });
  await b.state(sid, "cancelled");
  pass(
    "Question/answer and waiting-turn cancellation through the separate control path",
  );
  const boundary = await create("Sandbox boundary");
  const cmd =
    'if result=$(cat /opt/probe-denied/sentinel 2>&1); then echo UNEXPECTED_ACCESS; exit 1; else case "$result" in *"Permission denied"*) echo DENIED_SENTINEL;; *) echo UNEXPECTED_ERROR; exit 2;; esac; fi; test ! -S /var/run/docker.sock && test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token && test -z "${OPENAI_API_KEY:-}" && echo BOUNDARIES_OK; grep -E "NoNewPrivs|Seccomp:" /proc/self/status';
  await turn(boundary, "run_shell", { command: cmd });
  await finish(boundary, "run_shell");
  const proof = await shellProof(boundary);
  assert.equal(proof.exit_code, 0);
  assert.match(proof.stdout, /DENIED_SENTINEL/);
  assert.match(proof.stdout, /BOUNDARIES_OK/);
  assert.match(proof.stdout, /NoNewPrivs:\s+1/);
  assert.match(proof.stdout, /Seccomp:\s+2/);
  pass(
    "Approved shell still receives kernel denial; no Docker socket, Kubernetes token or model key in tool workload",
  );
  await save("boundary-proof.json", {
    time: new Date().toISOString(),
    session: boundary,
    result: { stdout: proof.stdout, exit_code: proof.exit_code },
  });
  // Four sessions sharing an explicit fixture environment; concurrency, not tenancy.
  const concurrent = await Promise.all(
    Array.from({ length: 4 }, (_, n) => create("Concurrent " + n)),
  );
  await Promise.all(
    concurrent.map((s, n) =>
      turn(s, "write_file", {
        path: "parallel-" + s + ".txt",
        content: String(n),
      }),
    ),
  );
  const parked = await Promise.all(
    concurrent.map((s) => a.state(s, "waiting")),
  );
  await Promise.all(
    concurrent.map((s, n) => b.approve(s, parked[n], "write_file")),
  );
  await Promise.all(concurrent.map((s) => a.state(s, "completed")));
  for (const s of concurrent) {
    const r = await toolResult(s, "write_file");
    assert.equal(r.events.filter((e) => e.type === "tool_finished").length, 1);
  }
  pass(
    "Four concurrent sessions, bounded worker slots and one committed tool result per task",
  );
  const catalog = JSON.parse(await admin(m, ["worker-pools"]));
  assert.ok(
    catalog.pools.every((p) => p.sessions_without_advertised_worker === 0),
  );
  await save("current-http-qualification.json", {
    status: "completed",
    time: new Date().toISOString(),
    checks,
    session: sid,
    artifact: artifact.id,
    artifact_sha256: sha,
    concurrent,
    catalog,
  });
} finally {
  for (const f of owned) f.close();
  await save("current-http-progress.json", { checks, session: sid });
}
