// Invoked by server-reference service_v1 qualification with disposable credentials.
import assert from "node:assert/strict";
import { Harness } from "../dist/index.js";
const s = await Harness.connect({
  url: process.env.OW_SERVICE_URL,
  namespace: "test",
  credentials: { tokenEnv: "OW_SERVICE_TOKEN" },
});
try {
  const session = await s.resume(JSON.parse(process.env.OW_SERVICE_REFERENCE));
  const run = await session.send(
    `TEST_RESOURCE_GRANTS ${JSON.stringify([{ name: "read_file", arguments: { path: "service-proof.txt" } }])}`,
    { idempotencyKey: "ts-read" },
  );
  let result = await run.result();
  if (result.status === "interrupted") {
    const i = result.interruptions[0];
    await session.control(
      run.id,
      {
        kind: "response",
        interaction_id: i.id,
        response: "once",
        binding: Object.fromEntries(
          [
            "bundle_digest",
            "schema_digest",
            "policy_digest",
            "resource_policy_digest",
          ].map((k) => [k, i[k]]),
        ),
      },
      { idempotencyKey: "ts-read-approve" },
    );
    for await (const e of s.events(session.id, result.cursor)) {
      if (e.type === "turn_completed") break;
    }
    result = await run.snapshot();
  }
  assert.equal(result.status, "completed");
  const events = [];
  for await (const e of run.events()) events.push(e.event);
  assert.ok(
    JSON.stringify(events).includes("real tool across API nodes"),
    JSON.stringify(events),
  );
  const agent = await s.agent("builder");
  await assert.rejects(
    agent.newSession({
      environmentId: process.env.OW_SERVICE_ENV,
      workingDirectory: "unsupported",
    }),
    /unsupported_session_option/,
  );
  const cancelled = await session.send(
    'TEST_RESOURCE_GRANTS [{"name":"write_file","arguments":{"path":"cancelled.txt","content":"must not write"}}]',
    { idempotencyKey: "ts-cancel" },
  );
  const waiting = await cancelled.result();
  assert.equal(waiting.status, "interrupted");
  const receipt = await session.control(
    cancelled.id,
    { kind: "cancel" },
    { idempotencyKey: "ts-cancel-command" },
  );
  for await (const e of s.events(session.id, waiting.cursor)) {
    if (e.type === "turn_completed") break;
  }
  assert.equal((await cancelled.snapshot()).status, "cancelled");
  assert.equal(
    (await session.controlReceipt(receipt.command.request_id)).status,
    "applied",
  );
  assert.equal((await run.snapshot()).status, "completed");
  console.log(
    "PASS TypeScript remote resume, real file read, event replay, cancellation and older-run result",
  );
} finally {
  s.disconnect();
}
