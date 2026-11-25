// Run against an isolated Spark qualification install. The recovery image drops
// its exec stream after the cluster has journaled the pending approval batch.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";
import { Harness } from "../../harness/typescript-sdk/dist/index.js";

const [mode, profileName, root, url, attempt = "1"] = process.argv.slice(2);
assert(["recovery", "live"].includes(mode));
assert(["sandboxedtools", "sandboxedloop"].includes(profileName));
assert.equal(new URL(url).hostname, "127.0.0.1");
const svc = await Harness.connect({
  url,
  namespace: "trial",
  credentials: {
    getToken: async () => (await readFile(root + "/admin", "utf8")).trim(),
  },
});
assert.equal(svc.capabilities.serviceId, "sandboxedloop-trial");
const id = profileName + "@1";
const slug = "agent-" + profileName + "@1";
const pin = {
  id,
  provider: "openshell",
  cpu: 1,
  memoryMB: 512,
  sharing: "exclusive",
  approvalMode: "ask",
  allowWorkspaceReads: ["src"],
  loopPlacement: profileName === "sandboxedloop" ? "sandbox" : "service",
};
assert(/^[1-9][0-9]?$/.test(attempt));
const key = (label) => ({ idempotencyKey: label + "-" + profileName });
async function waitFor(probe, description) {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const result = await probe();
    if (result) return result;
    await delay(250);
  }
  throw new Error("Timed out waiting for " + description);
}
async function answer(session, run, i, decision) {
  const binding = Object.fromEntries(
    [
      "bundle_digest",
      "schema_digest",
      "policy_digest",
      "resource_policy_digest",
    ].map((k) => [k, i[k]]),
  );
  return session.control(
    run.id,
    { kind: "response", interaction_id: i.id, response: decision, binding },
    key("response-" + i.id),
  );
}
try {
  await svc.registerRuntimeProfile(pin, key("profile"));
  await svc.activateRuntimeProfile(id, {
    ...key("activate-profile"),
    alias: profileName,
  });
  const release = {
    id: slug,
    instructions:
      "Use the supplied tools to complete the user request. Use exact relative paths.",
    tools: ["write_file", "read_file"],
    defaultRuntimeProfile: id,
    runtimeProfiles: [id],
  };
  if (mode === "live")
    release.defaultModel = { connection: "primary", model: "qwen3-coder-next" };
  await svc.deployAgent(release, key("release"));
  await svc.activateAgent(slug, key("activate-release"));
  const session = await (
    await svc.agent(slug)
  ).newSession({
    runtimeProfile: profileName,
    project: "qualification",
    ...key("session-" + attempt),
    waitTimeoutMs: 180000,
  });
  const ref = session.reference();
  const file = "src/approved.txt",
    marker = "Hello Spark";
  const task =
    mode === "live"
      ? 'Call write_file exactly once with path "src/approved.txt" and content "Hello Spark". After the tool result, stop and say done.'
      : "TEST_RESOURCE_GRANTS " +
        JSON.stringify([
          [
            { name: "write_file", arguments: { path: file, content: marker } },
            {
              name: "write_file",
              arguments: {
                path: "src/denied.txt",
                content: "should never be written",
              },
            },
          ],
          { name: "read_file", arguments: { path: file } },
        ]);
  const run = await session.send(task, key("turn"));
  const path = root + "/evidence/" + profileName + "-" + attempt;
  await writeFile(path + "-ref.json", JSON.stringify(ref));
  const state = async () => {
    const s = await run.snapshot();
    assert(
      !["cancelled", "failed"].includes(s.status),
      "Turn failed: " + s.failure,
    );
    return s;
  };
  const first = await waitFor(async () => {
    const s = await state();
    return s.status === "interrupted" && s.interruptions.length && s;
  }, "approval");
  const events = async () =>
    (
      await svc.json(
        "GET",
        "/v1/sessions/" + session.id + "/events/page?after=0",
      )
    ).events;
  if (mode === "recovery") {
    assert.equal(first.interruptions.length, 2);
    assert.notEqual(ref.loopEnvironmentId, ref.environmentId);
    await waitFor(
      async () =>
        (await events()).some(
          (e) =>
            e.type === "attachment_settled" &&
            e.data.state === "released" &&
            e.data.outcome_unknown === true,
        ),
      "actual stream loss during approval",
    );
    const after = await svc.resume(ref);
    const resumed = await after.resumeRun(run.submissionId);
    const waiting = await resumed.snapshot();
    assert.deepEqual(
      waiting.interruptions.map((i) => i.id).sort(),
      first.interruptions.map((i) => i.id).sort(),
    );
    // Resolve the second decision first and verify both decisions remain independent.
    for (const i of [...waiting.interruptions].reverse()) {
      await answer(
        after,
        resumed,
        i,
        JSON.parse(i.call.arguments).path === file ? "once" : "deny",
      );
      await waitFor(
        async () =>
          !(await resumed.snapshot()).interruptions.some((x) => x.id === i.id),
        "persisted decision",
      );
    }
  } else {
    assert.equal(first.interruptions.length, 1);
    assert.equal(first.interruptions[0].call.name, "write_file");
    assert.equal(JSON.parse(first.interruptions[0].call.arguments).path, file);
    await answer(session, run, first.interruptions[0], "once");
  }
  await waitFor(
    async () => (await state()).status === "completed",
    "turn completion",
  );
  const log = await events();
  const executed = log.filter(
    (e) => e.type === "tool_finished" && e.data.executed,
  );
  assert.equal(executed.filter((e) => e.data.name === "write_file").length, 1);
  assert(executed.every((e) => !e.data.result?.isError));
  if (mode === "recovery") {
    assert(
      log.some(
        (e) =>
          e.type === "interaction_resolved" &&
          e.data.origin === "workspace_read_grant",
      ),
    );
    assert(
      executed.some(
        (e) =>
          e.data.name === "read_file" &&
          JSON.stringify(e.data.result).includes(marker),
      ),
    );
    assert(
      log.some(
        (e) =>
          e.type === "tool_finished" &&
          e.data.name === "write_file" &&
          !e.data.executed,
      ),
    );
  }
  const evidence = {
    mode,
    profileName,
    sessionId: ref.sessionId,
    toolSandbox: ref.environmentId,
    loopSandbox: ref.loopEnvironmentId,
    pending: first.interruptions.map((i) => i.id),
    executed: executed.map((e) => ({ name: e.data.name, id: e.data.call_id })),
    streamLoss: log.filter(
      (e) =>
        e.type === "attachment_settled" &&
        e.data.state === "released" &&
        e.data.outcome_unknown === true,
    ).length,
    finalStatus: (await state()).status,
  };
  await writeFile(
    path + "-result.json",
    JSON.stringify(evidence, null, 2) + "\n",
  );
  console.log(JSON.stringify(evidence));
} finally {
  svc.disconnect();
}
