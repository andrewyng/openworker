// Destructive only to the explicitly owned qualification workers. Real tool
// effects are synthetic, unique-path writes in the shared test environment.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { repo, run, kubectl } from "../helm/local.mjs";
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
  permitPodIdentity,
  deploymentPods,
  hasFixtureWorker,
} from "./qualification-lib.mjs";
const m = await context(),
  checks = [],
  forwards = [];
const values = JSON.parse(await readFile(join(root, "values.json"), "utf8"));
const images = JSON.parse(
  await readFile(join(root, "variant-images.json"), "utf8"),
);
const environment = JSON.parse(
  await readFile(join(root, "environment.json"), "utf8"),
);
const k = (args) => kubectl(m, ["-n", "ow-app", ...args]);
const pass = (s) => {
  checks.push(s);
  console.log("PASS " + s);
};
const rollout = (release, role) =>
  k([
    "rollout",
    "status",
    `deployment/${release}-openworker-${role}`,
    "--timeout=180s",
  ]);
async function api(release) {
  const p = await wait(async () => (await pods(m, "api", release))[0]);
  const f = await forward(m, p.metadata.name);
  forwards.push(f);
  return client(f.port);
}
async function create(c, label) {
  return (
    await c.json("/sessions", {
      request_id: randomUUID(),
      parameters: {
        name: label,
        coworker: "task-desk",
        workspace: "/sandbox",
        environment_id: environment.id,
      },
    })
  ).session_id;
}
const turn = (c, sid, tool, args) =>
  c.json(`/sessions/${sid}/turns`, {
    submission_id: randomUUID(),
    text: planTool(tool, args),
  });
const sandboxRead = (path) =>
  kubectl(m, [
    "-n",
    "ow-shell",
    "exec",
    "default--" + environment.binding.remote_name,
    "-c",
    "agent",
    "--",
    "cat",
    "/sandbox/" + path,
  ]);
async function installVariant() {
  await permitPodIdentity(m, "variant");
  const v = structuredClone(values);
  v.image = images.harness;
  v.api.replicas = 1;
  v.worker.replicas = 1;
  v.taskDesk.enabled = false;
  await save("variant-values.json", v);
  await run(m.helm, [
    "upgrade",
    "--install",
    "variant",
    join(repo, "deploy/helm/openworker"),
    "--kubeconfig",
    m.kubeconfig,
    "--kube-context",
    m.context,
    "-n",
    "ow-app",
    "-f",
    join(root, "variant-values.json"),
  ]);
  await rollout("variant", "api");
  await rollout("variant", "worker");
}
let a, b, oldId, newId;
try {
  a = await api("ow");
  if (process.argv.includes("--resume-crash")) {
    const previous = JSON.parse(
      await readFile(join(root, "current-operations-progress.json"), "utf8"),
    );
    assert.equal(
      previous.checks.length,
      5,
      "Resume only after the preceding five gates passed in this experiment",
    );
    checks.push(...previous.checks);
    oldId = previous.oldId;
    newId = previous.newId;
    await k(["scale", "deployment/ow-openworker-worker", "--replicas=1"]);
    await rollout("ow", "worker");
    await wait(
      async () =>
        deploymentPods(
          JSON.parse(
            await k([
              "get",
              "pods",
              "-l",
              "app.kubernetes.io/instance=ow,app.kubernetes.io/component=worker",
              "-o",
              "json",
            ]),
          ).items,
        ).length === 1,
    );
  } else {
    oldId = await create(a, "Pinned old cohort");
    const oldFile = "old-" + randomUUID() + ".txt";
    await turn(a, oldId, "write_file", {
      path: oldFile,
      content: "old cohort",
    });
    const pending = await a.state(oldId, "waiting"),
      binding = pending.interactions[0].bundle_digest;
    await installVariant();
    b = await api("variant");
    newId = await create(b, "Pinned new cohort");
    await turn(b, newId, "write_file", {
      path: "new-" + randomUUID() + ".txt",
      content: "new cohort",
    });
    const newer = await b.state(newId, "waiting");
    assert.notEqual(newer.interactions[0].bundle_digest, binding);
    await b.approve(newId, newer, "write_file");
    await b.state(newId, "completed");
    assert.deepEqual(
      (await b.snapshot(oldId)).interactions,
      pending.interactions,
    );
    pass(
      "Two immutable coworker versions coexist; new API preserves the old approval exactly",
    );
    await k(["scale", "deployment/ow-openworker-worker", "--replicas=0"]);
    await wait(
      async () =>
        !deploymentPods(
          JSON.parse(
            await k([
              "get",
              "pods",
              "-l",
              "app.kubernetes.io/instance=ow,app.kubernetes.io/component=worker",
              "-o",
              "json",
            ]),
          ).items,
        ).length,
    );
    await b.approve(oldId, pending, "write_file");
    await turn(b, oldId, "read_file", { path: oldFile });
    const uncovered = await wait(async () => {
      const c = JSON.parse(await admin(m, ["worker-pools"], "variant"));
      const p = c.pools.find((p) => p.execution_digest === binding);
      // A live-model worker can share the bundle digest, but cannot claim this
      // scripted session. Coverage depends on the full selector, not pool count.
      return p && !hasFixtureWorker(p) && { c, p };
    });
    assert.equal(uncovered.p.pending_controls, 1);
    assert.equal(uncovered.p.queued_turns, 1);
    assert.ok(uncovered.p.sessions_without_advertised_worker >= 1);
    assert.deepEqual(
      (await b.snapshot(oldId)).interactions,
      pending.interactions,
    );
    await save("uncovered-pool.json", uncovered.c);
    pass(
      "Missing original pool is diagnosed; new workers do not consume its control or queued turn",
    );
    await k(["scale", "deployment/ow-openworker-worker", "--replicas=1"]);
    await rollout("ow", "worker");
    const resumed = await wait(async () => {
      const s = await b.snapshot(oldId);
      return s.turn?.status === "waiting" && s.turn.id !== pending.turn.id && s;
    });
    await b.approve(oldId, resumed, "read_file");
    await b.state(oldId, "completed");
    assert.equal(await sandboxRead(oldFile), "old cohort");
    pass(
      "Restoring the original pool completes the saved approval and follow-up without identity rewrite",
    );
    // One original worker makes the active drain target unambiguous.
    const active = await create(a, "Graceful cloud drain"),
      drainFile = "drain-" + randomUUID() + ".txt";
    await turn(a, active, "run_shell", {
      command: `sleep 50; printf 'one\\n' >> ${drainFile}`,
    });
    await a.approve(active, await a.state(active, "waiting"), "run_shell");
    await a.state(active, "executing");
    const worker = (await pods(m, "worker"))[0].metadata.name;
    await k(["rollout", "restart", "deployment/ow-openworker-worker"]);
    await wait(
      async () =>
        JSON.parse(await k(["get", "pod", worker, "-o", "json"])).metadata
          .deletionTimestamp,
    );
    assert.equal((await a.snapshot(active)).turn.status, "executing");
    assert.equal(
      await k([
        "exec",
        worker,
        "-c",
        "harness",
        "--",
        "curl",
        "--silent",
        "--output",
        "/dev/null",
        "--write-out",
        "%{http_code}",
        "--max-time",
        "2",
        "http://127.0.0.1:8081/readyz",
      ]),
      "503",
    );
    await a.state(active, "completed");
    await rollout("ow", "worker");
    assert.equal(await sandboxRead(drainFile), "one\n");
    pass(
      "Real Kubernetes rollout: old worker reports 503 while draining, finishes once, then exits",
    );
    // Roll back creation only. Keep both worker pools for already-created sessions.
    await k([
      "set",
      "image",
      "deployment/variant-openworker-api",
      "harness=" + values.image,
    ]);
    await rollout("variant", "api");
    b = await api("variant");
    const reverted = await create(b, "Creation rollback");
    await turn(b, reverted, "write_file", {
      path: "rollback-" + randomUUID(),
      content: "rollback",
    });
    const rp = await b.state(reverted, "waiting");
    assert.equal(rp.interactions[0].bundle_digest, binding);
    await b.approve(reverted, rp, "write_file");
    await b.state(reverted, "completed");
    pass(
      "API-only creation rollback selects old identity without removing the new-session worker pool",
    );
  }
  // Abrupt process death AFTER an external effect starts. Do not replay it.
  const crash = await create(a, "Unknown effect recovery"),
    crashFile = "crash-" + randomUUID() + ".txt";
  await turn(a, crash, "run_shell", {
    command: `printf 'one\\n' >> ${crashFile}; sleep 90`,
  });
  await a.approve(crash, await a.state(crash, "waiting"), "run_shell");
  await a.state(crash, "executing");
  await wait(async () => {
    try {
      return (await sandboxRead(crashFile)) === "one\n";
    } catch {
      return false;
    }
  });
  const victim = (await pods(m, "worker"))[0].metadata.name;
  // PID 1 in a PID namespace can ignore a SIGKILL sent from that same
  // namespace. Ask kubelet to terminate this exact disposable worker instead.
  await k([
    "delete",
    "pod",
    victim,
    "--grace-period=0",
    "--force",
    "--wait=false",
  ]);
  const recovered = await wait(async () => {
    const s = await a.snapshot(crash);
    return s.turn?.status === "interrupted" && s;
  }, 180000);
  const events = await a.events(crash, recovered.cursor);
  const recovery = events.filter((e) => e.type === "turn_recovered").at(-1);
  assert.equal(recovery?.data.outcome_unknown_count, 1);
  assert.equal(recovery.data.automatic_replay, false);
  assert.equal(events.filter((e) => e.type === "tool_started").length, 1);
  assert.equal(events.filter((e) => e.type === "tool_finished").length, 0);
  assert.equal(await sandboxRead(crashFile), "one\n");
  pass(
    "Abrupt worker death after a real effect yields unknown outcome, never an automatic tool replay",
  );
  await k(["scale", "deployment/ow-openworker-worker", "--replicas=2"]);
  await rollout("ow", "worker");
  const catalog = JSON.parse(await admin(m, ["worker-pools"]));
  assert.ok(
    catalog.pools.every((p) => p.sessions_without_advertised_worker === 0),
  );
  await save("current-operations-qualification.json", {
    status: "completed",
    time: new Date().toISOString(),
    checks,
    oldId,
    newId,
    crash,
    catalog,
    limits: [
      "Same native binary, distinct bundled coworker releases; not an arbitrary native-version migration",
      "Process failure and Deployment rollout, not an EC2 node/AZ outage",
    ],
  });
} finally {
  for (const f of forwards) f.close();
  await save("current-operations-progress.json", { checks, oldId, newId });
  // Retain both pinned pools. Do not uninstall the variant while its sessions exist.
  await k(["scale", "deployment/ow-openworker-worker", "--replicas=2"]).catch(
    () => {},
  );
}
