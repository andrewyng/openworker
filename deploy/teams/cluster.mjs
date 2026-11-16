// Real Kubernetes/OpenShell team qualification. Explicit owned local fixture only.
// Mutations are issued once; lost outcomes require inspecting saved identities.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve, join, basename } from "node:path";
import { spawn } from "node:child_process";
import { Harness } from "../../harness/typescript-sdk/dist/index.js";
import { run as command } from "../service/stack.mjs";
const [directory, confirmation, output] = process.argv.slice(2);
assert(
  directory && confirmation && output,
  "cluster.mjs OWNED_SERVICE_STATE EXACT_SERVICE_ID NEW_IGNORED_OUTPUT",
);
const state = resolve(directory),
  out = resolve(output),
  m = JSON.parse(await readFile(join(state, "kubernetes.json")));
assert.equal(m.id, confirmation);
assert.match(m.namespace, /^ow-service-m4-resilienceteams/);
assert.match(m.context, /^kind-/);
assert.equal(
  (await command("git", ["check-ignore", join(out, "private")])).trim(),
  join(out, "private"),
);
await mkdir(out, { mode: 0o700 });
const save = (name, value) =>
  writeFile(join(out, name + ".json"), JSON.stringify(value, null, 2), {
    mode: 0o600,
    flag: "wx",
  });
const kub = (args) =>
  command("kubectl", [
    "--kubeconfig",
    m.kubeconfig,
    "--context",
    m.context,
    ...args,
  ]);
const kube = (args) => kub(["-n", m.namespace, ...args]);
const profileId = "team-" + basename(out) + "@1";
const releaseId = (role) => role + "-" + basename(out) + "@1";
const token = String(await readFile(join(state, "input/admin-token"))).trim();
const report = {
  passed: false,
  serviceId: m.id,
  checks: [],
  sessions: [],
  environmentId: null,
  cleanup: {},
};
const baseline = JSON.parse(await kub(["get", "pods", "-A", "-o", "json"]))
  .items.filter(
    (p) =>
      p.metadata.namespace !== m.namespace &&
      (p.metadata.name.includes("--owr-") ||
        p.metadata.name.startsWith("os-supervisor-")),
  )
  .map((p) => p.metadata.uid)
  .sort();
await save("baseline-sandbox-pods", baseline);
async function until(f, label, ms = 180000) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) {
    try {
      const v = await f();
      if (v) return v;
    } catch (e) {
      last = e;
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(label + ": " + (last?.message ?? "timeout"));
}
const forwarding = spawn(
  "kubectl",
  [
    "--kubeconfig",
    m.kubeconfig,
    "--context",
    m.context,
    "-n",
    m.namespace,
    "port-forward",
    "service/" + m.release + "-api",
    `${m.port}:443`,
    "--address",
    "127.0.0.1",
  ],
  { stdio: "ignore" },
);
let h;
try {
  h = await until(async () => {
    try {
      return await Harness.connect({
        url: `https://localhost:${m.port}`,
        namespace: "local",
        credentials: { getToken: async () => token },
      });
    } catch {
      return false;
    }
  }, "API");
  assert.equal(h.capabilities.serviceId, m.id);
  assert.equal(h.capabilities.features.teams, true);
  for (const role of ["api", "worker"]) {
    const d = JSON.parse(
      await kube(["get", "deployment", m.release + "-" + role, "-o", "json"]),
    );
    assert.equal(
      d.spec.template.spec.containers.find((c) => c.name === "harness").image,
      m.imageRef,
    );
  }
  const worker = JSON.parse(
    await kube(["get", "deployment", m.release + "-worker", "-o", "json"]),
  );
  assert.equal(worker.spec.replicas, 1);
  assert.equal(
    worker.spec.template.spec.containers
      .find((c) => c.name === "harness")
      .args.at(4),
    "1",
    "qualify exactly one execution slot",
  );
  await h.registerRuntimeProfile(
    {
      id: profileId,
      provider: "openshell",
      cpu: 1,
      memoryMB: 512,
      sharing: "shared",
      approvalMode: "bypass",
      autoSkipQuestions: true,
    },
    { idempotencyKey: "team-profile-" + basename(out) },
  );
  await h.activateRuntimeProfile(profileId, {
    idempotencyKey: "team-profile-activate-" + basename(out),
  });
  for (const id of ["lead", "builder", "verifier"]) {
    await h.deployAgent(
      {
        id: releaseId(id),
        instructions:
          "Follow the explicit qualification plan. Coordinate via named instances and mention-only board messages. Work in the original shared environment. Never repeat an uncertain external effect.",
        tools:
          id === "lead"
            ? ["read_file"]
            : [
                "read_file",
                "write_file",
                "run_shell",
                "process_start",
                "process_status",
                "process_logs",
                "process_stop",
              ],
        runtimeProfiles: [profileId],
        defaultRuntimeProfile: profileId,
      },
      { idempotencyKey: "release-" + releaseId(id) },
    );
    await h.activateAgent(releaseId(id), { idempotencyKey: "activate-" + releaseId(id) });
  }
  const agent = await h.agent(releaseId("lead"));
  const session = await agent.newSession({
    project: "teams",
    name: "Lead builder verifier",
    idempotencyKey: "team-session-" + basename(out),
    waitTimeoutMs: 180000,
  });
  report.sessions.push(session.id);
  report.environmentId = session.environmentId;
  await save("original-session", session.reference());
  const team = await session.createTeam({
    agentTypes: { builder: releaseId("builder"), verifier: releaseId("verifier") },
  });
  await save("original-team", await team.snapshot());
  const plan = (steps) => "TEST_RESOURCE_GRANTS " + JSON.stringify(steps);
  const build = plan([
    {
      name: "run_shell",
      arguments: {
        command:
          'mkdir -p game; printf "<!doctype html><title>Team Arcade</title><h1>Built by a team</h1><button onclick=\"this.textContent=123\">Play</button>" > game/index.html',
        yield_ms: 10000,
      },
    },
    {
      name: "team_post_board",
      arguments: { message: "@lead Built game/index.html. Ready to verify." },
    },
  ]);
  const verify = plan([
    {
      name: "run_shell",
      arguments: {
        command:
          'test -f game/index.html && grep -q "Built by a team" game/index.html && printf "verified\n" > game/verification.txt && cat game/verification.txt',
        yield_ms: 10000,
      },
    },
    {
      name: "team_post_board",
      arguments: {
        message:
          "@lead Verified game/index.html and wrote game/verification.txt.",
      },
    },
  ]);
  const steps = [
    {
      name: "team_spawn",
      arguments: {
        agent_type: "builder",
        agent_id: "builder-ui",
        message: build,
      },
    },
    {
      name: "team_wait_agent",
      arguments: { agent_ids: ["builder-ui"], timeout_ms: 120000 },
    },
    {
      name: "team_spawn",
      arguments: {
        agent_type: "verifier",
        agent_id: "verifier-ui",
        message: verify,
      },
    },
    {
      name: "team_wait_agent",
      arguments: { agent_ids: ["verifier-ui"], timeout_ms: 120000 },
    },
  ];
  await save("lead-intent", { sessionId: session.id, text: plan(steps) });
  const run = await session.send(plan(steps), { idempotencyKey: "lead-task" });
  await save("lead-run", { id: run.id, submissionId: run.submissionId });
  const final = await until(async () => {
    const r = await run.snapshot();
    if (r.status === "running") return false;
    assert.equal(r.status, "completed", JSON.stringify(r));
    return r;
  }, "lead completion");
  await save("lead-result", final);
  const doc = await team.snapshot();
  await save("team-after", doc);
  assert.equal(Object.keys(doc.roster.instances).length, 3);
  const snapshots = {};
  for (const [id, instance] of Object.entries(doc.roster.instances)) {
    const s = await until(async () => {
      const s = await h.json("GET", `/v1/sessions/${instance.session_id}`);
      return [
        "completed",
        "failed",
        "cancelled",
        "interrupted",
        "truncated",
      ].includes(s.turn?.status)
        ? s
        : false;
    }, "member " + id);
    assert.equal(s.turn.status, "completed", JSON.stringify(s));
    assert.equal(s.session.environment.environment_id, session.environmentId);
    snapshots[id] = s;
    report.sessions.push(instance.session_id);
  }
  await save("members", snapshots);
  const history = await team.history();
  await save("board", history);
  assert.equal(history.messages.length, 2);
  assert(
    history.messages.every(
      (v) => v.recipients.length === 1 && v.recipients[0] === "lead",
    ),
  );
  const allEvents = {};
  for (const [id, instance] of Object.entries(doc.roster.instances)) {
    const e = (
      await h.json(
        "GET",
        `/v1/sessions/${instance.session_id}/events/page?after=0`,
      )
    ).events;
    allEvents[id] = e;
    assert(!e.some((v) => v.type === "tool_outcome_unknown"));
  }
  await save("events", allEvents);
  const observations = {};
  for (const [id, snapshot] of Object.entries(snapshots)) {
    const sid = doc.roster.instances[id].session_id;
    observations[id] = await until(async () => {
      const summary = await h.json("GET", `/v1/sessions/${sid}/observability/${snapshot.turn.id}/summary`);
      return summary.summary.models.length ? summary : false;
    }, "recorded model accounting " + id);
    assert(observations[id].summary.tools.length > 0, "recorded coordination/execution tools");
  }
  await save("observability", observations);
  assert(
    allEvents.lead.filter((v) => v.type === "owner_claimed").length >= 3,
    "parent releases slots between children",
  );
  for (const id of ["builder-ui", "verifier-ui"]) {
    const tools = allEvents[id].filter(
      (e) => e.type === "tool_finished" && e.data.name === "run_shell",
    );
    assert.equal(tools.length, 1);
    assert.notEqual(tools[0].data.result.isError, true, JSON.stringify(tools));
  }
  report.checks.push(
    "one-slot lead → builder → verifier",
    "one original shared OpenShell environment",
    "real file creation and independent shell verification",
    "mentions delivered to next LLM continuation",
    "durable waits and worker-lease transfer",
    "no duplicate external tools",
  );
  // A lost-read/reconnect does not start another agent or execute tools.
  h.disconnect();
  h = await Harness.connect({
    url: `https://localhost:${m.port}`,
    namespace: "local",
    credentials: { getToken: async () => token },
  });
  const restored = await h.resume(session.reference());
  const reattached = await restored.resumeTeam();
  assert.equal((await reattached.snapshot()).id, doc.id);
  assert.equal((await reattached.history()).messages.length, 2);
  report.checks.push(
    "service SDK reconnect uses original team/session identities",
  );
  await reattached.stop("stop-original-team");
  // A second team shares the same environment but has a parked live child.
  // Team stop must settle it through the normal cancellation control path.
  const stopping = await (await h.agent(releaseId("lead"))).newSession({
    environmentId: session.environmentId,
    project: "teams",
    name: "Parked child cancellation",
    idempotencyKey: "stop-team-session-" + basename(out),
  });
  report.sessions.push(stopping.id);
  await save("stop-session", stopping.reference());
  const stopTeam = await stopping.createTeam({agentTypes:{builder:releaseId("builder")}});
  const parked = await stopTeam.spawn("spawn-parked", "builder", "builder-parked", plan([
    {name:"team_wait_agent",arguments:{agent_ids:["lead"],timeout_ms:120000}},
  ]));
  const parkedId = parked.instance.session_id;
  report.sessions.push(parkedId);
  await save("parked-child", parked);
  await until(async () => (await h.json("GET", `/v1/sessions/${parkedId}`)).turn?.status === "waiting_agents", "parked child");
  await stopTeam.stop("durable-stop-parked-team");
  const cancelled = await until(async () => {
    const snapshot = await h.json("GET", `/v1/sessions/${parkedId}`);
    return snapshot.turn?.status === "cancelled" ? snapshot : false;
  }, "cancel parked child");
  await save("cancelled-child", cancelled);
  await assert.rejects(stopTeam.spawn("after-stop", "builder", "late-builder", "Must not execute"));
  report.checks.push("durable team stop cancels a parked child and rejects new members");
  const limited = await (await h.agent(releaseId("lead"))).newSession({
    environmentId: session.environmentId,
    project: "teams", name: "Aggregate budget summary",
    idempotencyKey: "limited-team-" + basename(out),
  });
  report.sessions.push(limited.id);
  const limitedTeam = await limited.createTeam({
    agentTypes: {builder:releaseId("builder")},
    limits: {maxModelCalls:1,maxOutputTokens:1024},
  });
  await save("limited-session", limited.reference());
  const limitedRun = await limited.send(plan([
    {name:"team_post_board",arguments:{message:"First permitted action"}},
    {name:"team_post_board",arguments:{message:"Must not execute after limit"}},
  ]),{idempotencyKey:"limited-turn"});
  const limitedResult = await until(async()=>{
    const r=await limitedRun.snapshot();
    return r.status!=="running"?r:false;
  },"bounded team summary");
  assert.equal(limitedResult.status,"completed");
  const limitedSnapshot = await limited.snapshot();
  assert.equal(limitedSnapshot.turn.budget.stop_reason,"team_budget_exceeded");
  assert.equal(limitedSnapshot.turn.budget.charged_output_tokens,1024);
  assert.equal((await limitedTeam.history()).messages.length,1);
  await save("limited-result",{result:limitedResult,snapshot:limitedSnapshot});
  report.checks.push("aggregate budget returns a bounded summary without another tool effect");
  await limitedTeam.stop("limited-team-stop");
  for (const sid of new Set(report.sessions)) {
    await h.json("POST", `/v1/sessions/${sid}/close`, {}, "close-" + sid);
  }
  await h.json(
    "POST",
    `/v1/environments/${session.environmentId}/delete`,
    {},
    "delete-original-team-environment",
  );
  await until(async () => {
    const e = await h.environment(session.environmentId);
    return e.environment.phase === "deleted";
  }, "owned environment cleanup");
  report.cleanup.environmentDeleted = true;
  await until(async () => {
  const after = JSON.parse(await kub(["get", "pods", "-A", "-o", "json"]))
    .items.filter(
      (p) =>
        p.metadata.namespace !== m.namespace &&
        (p.metadata.name.includes("--owr-") ||
          p.metadata.name.startsWith("os-supervisor-")),
    )
    .map((p) => p.metadata.uid)
    .sort();
  return JSON.stringify(after) === JSON.stringify(baseline);
  }, "sandbox pods finish asynchronous deletion");
  report.cleanup.unrelatedSandboxesUnchanged = true;
  report.passed = true;
} finally {
  h?.disconnect();
  forwarding.kill("SIGTERM");
  await save("report", report);
  console.log(JSON.stringify(report));
}
