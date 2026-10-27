import assert from "node:assert/strict";
import { Harness } from "../dist/index.js";
const service = await Harness.connect({
  url: process.env.OW_SERVICE_URL,
  namespace: "test",
  credentials: { tokenEnv: "OW_SERVICE_TOKEN" },
});
try {
  const reference = JSON.parse(process.env.OW_SERVICE_REFERENCE);
  const coder = await service.resume(reference);
  await assert.rejects(service.resume({...reference,agent:'verifier@1'}),/release_identity_mismatch/);
  const verifier = await service.agent("verifier");
  assert.equal(verifier.id, "verifier@1");
  await assert.rejects(
    service.registerRuntimeProfile({
      id: "bad@1",
      provider: "openshell",
      cpu: 1,
      memoryMB: 512,
      sharing: "shared",
    }),
    (e) => e.status === 403,
  );
  await assert.rejects(
    verifier.newSession({
      environmentId: coder.environmentId,
      project: "different",
      idempotencyKey: "wrong-project",
    }),
    (e) => e.status === 403,
  );
  const session = await verifier.newSession({
    environmentId: coder.environmentId,
    project: "game",
    idempotencyKey: "verify-create",
  });
  assert.notEqual(session.id, coder.id);
  assert.equal(session.environmentId, coder.environmentId);
  const retry = await verifier.newSession({
    environmentId: coder.environmentId,
    project: "game",
    idempotencyKey: "verify-create",
  });
  assert.equal(retry.id, session.id);
  const run = await session.send(
    'TEST_RESOURCE_GRANTS [{"name":"read_file","arguments":{"path":"shared-proof.txt"}}]',
    { idempotencyKey: "read" },
  );
  assert.equal((await run.result()).status, "completed");
  let output = "";
  for await (const e of run.events()) output += JSON.stringify(e.event);
  assert.ok(output.includes("coder output for verifier"));
  const snapshot = await session.snapshot();
  assert.ok(!JSON.stringify(snapshot.messages).includes("write_file"));
  assert.equal(snapshot.session.permissions.mode, "bypass");
  const before = await coder.snapshot();
  assert.ok(!JSON.stringify(before.messages).includes("read_file"));
  let operationId;
  try {
    await verifier.newSession({
      project: "isolated",
      idempotencyKey: "isolated-create",
      waitTimeoutMs: 1,
    });
  } catch (error) {
    assert.equal(error.code, "provisioning_pending");
    assert.ok(error.operationId);
    operationId = error.operationId;
  }
  const isolated = await verifier.newSession({
    project: "isolated",
    idempotencyKey: "isolated-create",
  });
  assert.notEqual(isolated.environmentId, coder.environmentId);
  if (operationId)
    assert.equal(
      (await service.operation(operationId)).receipt.sessionId,
      isolated.id,
    );
  console.log(
    "PASS TypeScript shared verifier, independent histories, profile permissions, owner/project gates and idempotency",
  );
} finally {
  service.disconnect();
}
