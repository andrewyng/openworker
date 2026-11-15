// Exercise the shipped CLI against the container stack. No model key required.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { load, compose, run } from "./stack.mjs";
const [directory, binary] = process.argv.slice(2);
if (!directory || !binary)
  throw Error("Usage: node deploy/service/qualify.mjs STATE /absolute/ows");
const qualificationId = Date.now().toString(36);
const m = await load(directory),
  cliBinary = resolve(binary),
  config = join(m.root, "cli.json");
async function cli(args, expectFailure = false) {
  args = [...args];
  const keyAt = args.indexOf("--idempotency-key");
  if (keyAt >= 0) args[keyAt + 1] = qualificationId + "-" + args[keyAt + 1];
  const result = await new Promise((ok, no) => {
    const p = spawn(cliBinary, ["--config", config, ...args]);
    let stdout = "",
      stderr = "";
    p.stdout.on("data", (b) => (stdout += b));
    p.stderr.on("data", (b) => (stderr += b));
    p.on("error", no);
    const timer = setTimeout(() => p.kill("SIGTERM"), 180000);
    p.on("close", (code) => {
      clearTimeout(timer);
      ok({ code, stdout, stderr });
    });
  });
  if (expectFailure) {
    assert.notEqual(result.code, 0);
    return result;
  }
  assert.equal(result.code, 0, `ows ${args[0]} failed: ${result.stderr}`);
  const lines = result.stdout
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  return lines.at(-1);
}
async function ready() {
  for (let i = 0; i < 80; i++) {
    try {
      return await cli(["whoami"]);
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  throw Error("Service did not become ready");
}
async function file(name, value) {
  const p = join(m.root, name);
  await writeFile(
    p,
    typeof value === "string" ? value : JSON.stringify(value),
    { mode: 0o600 },
  );
  return p;
}
async function close(sid) {
  for (let i = 0; i < 50; i++) {
    try {
      return await cli([
        "close",
        "--session",
        sid,
        "--idempotency-key",
        `close-${sid}`,
      ]);
    } catch {
      await new Promise((r) => setTimeout(r, 200));
    }
  }
  throw Error("Session still owned after completion");
}
await ready();
await cli(["login"]);
await cli(["--ca-file", join(m.root, "input/pg-ca.crt"), "whoami"], true);
const invalidToken = await file(
  "invalid-token",
  "invalid-test-token-".repeat(4),
);
await cli(["--token-file", invalidToken, "whoami"], true);
assert.equal((await cli(["capabilities"])).features.combinedSubmission, true);
const profile = await file("profile.json", {
  id: "local@1",
  provider: "openshell",
  cpu: 1,
  memoryMB: 512,
  sharing: "shared",
  approvalMode: "ask",
});
const agent = await file("agent.json", {
  id: "builder@1",
  instructions: "Use the tools to complete the requested task.",
  tools: ["read_file", "write_file", "run_shell"],
  runtimeProfiles: ["local@1"],
  defaultRuntimeProfile: "local@1",
});
await cli([
  "profile",
  "deploy",
  "--file",
  profile,
  "--idempotency-key",
  "profile",
]);
await cli([
  "profile",
  "activate",
  "local@1",
  "--alias",
  "default",
  "--idempotency-key",
  "profile-activate",
]);
await cli(["agent", "deploy", "--file", agent, "--idempotency-key", "agent"]);
await cli([
  "agent",
  "activate",
  "builder@1",
  "--alias",
  "builder",
  "--idempotency-key",
  "agent-activate",
]);
const task = await file(
  "task.txt",
  'TEST_RESOURCE_GRANTS [{"name":"write_file","arguments":{"path":"cli-proof.txt","content":"ows container proof"}}]',
);
const args = [
  "submit",
  "--agent",
  "builder",
  "--project",
  "qualification",
  "--message-file",
  task,
  "--idempotency-key",
  "first-task",
];
const accepted = await cli(args);
const op = accepted.operation;
assert.ok(op.sessionId && op.environmentId && op.runId && op.submissionId);
const duplicate = await cli(args);
assert.equal(duplicate.operation.id, op.id);
assert.equal(duplicate.operation.runId, op.runId);
await writeFile(task, "different payload");
await cli(args, true);
await writeFile(
  task,
  'TEST_RESOURCE_GRANTS [{"name":"write_file","arguments":{"path":"cli-proof.txt","content":"ows container proof"}}]',
);
const provisioned = await cli(["operation", "wait", op.id]);
assert.equal(provisioned.receipt.runId, op.runId);
let watched = await cli([
  "watch",
  "--session",
  op.sessionId,
  "--run",
  op.runId,
]);
assert.equal(watched.result.status, "interrupted");
let snapshot = await cli(["status", "--session", op.sessionId]);
const approval = snapshot.interactions[0];
assert.equal(approval.kind, "approval");
await cli(["close", "--session", op.sessionId], true);
await cli(
  [
    "environment",
    "delete",
    op.environmentId,
    "--idempotency-key",
    "early-delete",
  ],
  true,
);
await compose(m, ["restart", "api", "worker"]);
await ready();
snapshot = await cli(["status", "--session", op.sessionId]);
assert.equal(snapshot.interactions[0].id, approval.id);
await cli([
  "approve",
  "--session",
  op.sessionId,
  "--interaction",
  approval.id,
  "--allow",
  "once",
  "--idempotency-key",
  "approve-write",
]);
// Wait for control application and completion through the durable stream.
for (let n = 0; n < 30; n++) {
  watched = await cli([
    "watch",
    "--session",
    op.sessionId,
    "--run",
    op.runId,
    "--after",
    String(snapshot.cursor),
  ]);
  if (watched.result.status === "completed") break;
  await new Promise((r) => setTimeout(r, 200));
}
assert.equal(watched.result.status, "completed");
const read = await file(
  "read.txt",
  'TEST_RESOURCE_GRANTS [{"name":"read_file","arguments":{"path":"cli-proof.txt"}}]',
);
const follow = await cli([
  "send",
  "--session",
  op.sessionId,
  "--message-file",
  read,
  "--idempotency-key",
  "read-proof",
]);
watched = await cli([
  "watch",
  "--session",
  op.sessionId,
  "--run",
  follow.runId,
]);
assert.equal(watched.result.status, "interrupted");
snapshot = await cli(["status", "--session", op.sessionId]);
await cli([
  "approve",
  "--session",
  op.sessionId,
  "--interaction",
  snapshot.interactions[0].id,
  "--allow",
  "once",
  "--idempotency-key",
  "approve-read",
]);
for (let n = 0; n < 30; n++) {
  watched = await cli([
    "watch",
    "--session",
    op.sessionId,
    "--run",
    follow.runId,
    "--after",
    String(snapshot.cursor),
  ]);
  if (watched.result.status === "completed") break;
  await new Promise((r) => setTimeout(r, 200));
}
assert.equal(watched.result.status, "completed");
// Use the CLI's full NDJSON replay to verify real tool bytes, not just status.
const replay = await run(cliBinary, [
  "--config",
  config,
  "watch",
  "--session",
  op.sessionId,
  "--run",
  follow.runId,
]);
assert.ok(replay.includes("ows container proof"));
const questionTask = await file(
  "question.txt",
  'TEST_RESOURCE_GRANTS [{"name":"ask_user","arguments":{"question":"Which color?","options":["blue","green"]}}]',
);
const question = await cli([
  "send",
  "--session",
  op.sessionId,
  "--message-file",
  questionTask,
  "--idempotency-key",
  "question",
]);
await cli(["watch", "--session", op.sessionId, "--run", question.runId]);
snapshot = await cli(["status", "--session", op.sessionId]);
const pendingQuestion =
  snapshot.interactions.find(
    (i) => i.status === "pending" && i.kind === "question",
  ) ?? snapshot.interactions.find((i) => i.kind === "question");
assert.ok(pendingQuestion);
const answer = await file("answer.txt", "blue");
await cli([
  "answer",
  "--session",
  op.sessionId,
  "--interaction",
  pendingQuestion.id,
  "--answer-file",
  answer,
  "--idempotency-key",
  "answer",
]);
for (let n = 0; n < 30; n++) {
  watched = await cli([
    "watch",
    "--session",
    op.sessionId,
    "--run",
    question.runId,
    "--after",
    String(snapshot.cursor),
  ]);
  if (watched.result.status === "completed") break;
  await new Promise((r) => setTimeout(r, 200));
}
assert.equal(watched.result.status, "completed");
const cancelTask = await file(
  "cancel.txt",
  'TEST_RESOURCE_GRANTS [{"name":"write_file","arguments":{"path":"must-not-write.txt","content":"cancel me"}}]',
);
const cancel = await cli([
  "send",
  "--session",
  op.sessionId,
  "--message-file",
  cancelTask,
  "--idempotency-key",
  "cancel-task",
]);
await cli(["watch", "--session", op.sessionId, "--run", cancel.runId]);
await cli([
  "cancel",
  "--session",
  op.sessionId,
  "--run",
  cancel.runId,
  "--idempotency-key",
  "cancel",
]);
for (let n = 0; n < 50; n++) {
  snapshot = await cli(["status", "--session", op.sessionId]);
  if (snapshot.turn.status === "cancelled") break;
  await new Promise((r) => setTimeout(r, 200));
}
assert.equal(snapshot.turn.status, "cancelled");
await compose(m, ["down"]);
await compose(m, ["up", "-d"], { inherit: true });
await ready();
assert.equal(
  (await cli(["status", "--session", op.sessionId])).turn.id,
  cancel.runId,
);
await close(op.sessionId);
let deleted;
for (let n = 0; n < 100; n++) {
  try {
    deleted = await cli([
      "environment",
      "delete",
      op.environmentId,
      "--idempotency-key",
      "delete-environment",
    ]);
    if (deleted.phase === "deleted") break;
  } catch {}
  await new Promise((r) => setTimeout(r, 1000));
}
assert.equal(deleted?.phase, "deleted");
const report = {
  passed: true,
  serviceId: m.id,
  sessionId: op.sessionId,
  environmentId: op.environmentId,
  runId: op.runId,
  checks: [
    "atomic submit + retry conflict",
    "TLS + authenticated CLI + rejected wrong CA/token",
    "durable question + answer",
    "real OpenShell write/read",
    "approval retained across API/worker restart",
    "cancellation",
    "compose down/up persistence",
    "safe close + explicit environment deletion",
  ],
};
await file("qualification.json", report);
console.log(JSON.stringify(report, null, 2));
