// Destructive-to-OWNED-PROCESSES qualification; never target customer state.
// Leaves example sessions/artifacts and one explicitly shared test environment.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID, createHash } from "node:crypto";
import { Readable } from "node:stream";
import { load, up, launch, until, command, compose, delay } from "./stack.mjs";
const m = await load(process.argv[2]);
await up(m);
const running = await launch(m);
const a = `http://127.0.0.1:${m.ports.apiA}`,
  b = `http://127.0.0.1:${m.ports.apiB}`;
const headers = { Authorization: `Bearer ${running.token}` },
  key = randomUUID();
const report = [];
const pass = (text) => {
  report.push(text);
  console.log("PASS " + text);
};
async function request(base, path, body) {
  const r = await fetch(base + path, {
    method: body ? "POST" : "GET",
    headers: { ...headers, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(15000),
  });
  assert.ok(r.ok, `${path}: ${r.status}`);
  return r.json();
}
const snap = (id) => request(b, `/sessions/${id}`);
const plan = (name, args) =>
  "TEST_RESOURCE_GRANTS " + JSON.stringify([{ name, arguments: args }]);
async function wait(id, status) {
  return until(async () => {
    const s = await snap(id);
    return s.turn?.status === status ? s : false;
  }, 90000);
}
async function approve(id, s) {
  const i = s.interactions[0];
  assert.ok(i);
  await request(a, `/sessions/${id}/controls`, {
    request_id: "answer-" + i.id,
    turn_id: i.turn_id,
    action: {
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
  });
}
async function events(id, through) {
  const abort = new AbortController(),
    timer = setTimeout(() => abort.abort(), 30000);
  const out = [];
  try {
    const r = await fetch(`${a}/sessions/${id}/events?after=0`, {
      headers,
      signal: abort.signal,
    });
    assert.ok(r.ok);
    let buffer = "";
    for await (const chunk of r.body) {
      buffer += Buffer.from(chunk).toString();
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const text = frame.split("\n").find((l) => l.startsWith("data: "));
        if (text) {
          const e = JSON.parse(text.slice(6));
          assert.equal(e.event_id, out.length + 1);
          out.push(e);
          if (e.event_id >= through) return out;
        }
      }
    }
    throw Error("SSE ended early");
  } finally {
    abort.abort();
    clearTimeout(timer);
  }
}
let env, checkpoint;
async function create(name, permissions) {
  const made = await request(a, "/sessions", {
    request_id: "create-" + randomUUID(),
    parameters: {
      name,
      coworker: "assistant-example",
      workspace: "/sandbox",
      environment_id: env.id,
      ...(permissions ? { permissions } : {}),
    },
  });
  return made.session_id;
}
async function submit(id, text) {
  return request(a, `/sessions/${id}/turns`, {
    submission_id: randomUUID(),
    text,
  });
}
async function restart(role) {
  await running.stop(role);
  await running.start(role);
}
try {
  env = JSON.parse(
    await command(
      running.bin,
      [
        "--config",
        running.config,
        "environment-create",
        "--request-id",
        "reference-sandbox",
        "--shared",
      ],
      { env: running.env },
    ),
  );
  assert.equal(env.phase, "ready");
  const sid = await create("Weekly release brief");
  await submit(
    sid,
    plan("write_file", {
      path: `brief-${key}.txt`,
      content:
        "Release readiness\n\n• Verify the migration\n• Review the deployment\n• Confirm rollback ownership\n",
    }),
  );
  const pending = await wait(sid, "waiting"),
    interaction = pending.interactions[0].id;
  await running.stop("worker-a", "SIGKILL");
  await running.stop("worker-b", "SIGKILL");
  await restart("api-a");
  await until(async () => {
    try {
      return (
        (await request(a, `/sessions/${sid}`)).interactions[0].id ===
        interaction
      );
    } catch {
      return false;
    }
  });
  await running.start("worker-a");
  await running.start("worker-b");
  await approve(sid, pending);
  await wait(sid, "completed");
  pass(
    "Approval identity survives worker crash and API replacement; response through another node resumes work",
  );
  await submit(sid, plan("read_file", { path: `brief-${key}.txt` }));
  const readPending = await wait(sid, "waiting");
  await approve(sid, readPending);
  const complete = await wait(sid, "completed");
  const history = await events(sid, complete.cursor);
  const result = history
    .filter((e) => e.type === "tool_finished" && e.data.name === "read_file")
    .at(-1).data.result;
  const bytes = Buffer.from(JSON.parse(result.content[0].text).text);
  assert.ok(bytes.includes("rollback ownership"));
  pass("Contiguous SSE replay and actual OpenShell file write/read");
  const hash = (v) => createHash("sha256").update(v).digest("hex");
  async function register(id, data, name) {
    return request(a, `/sessions/${id}/artifacts`, {
      request_id: randomUUID(),
      turn_id: complete.turn.id,
      name,
      content_type: "text/plain",
      size: data.length,
      sha256: hash(data),
    });
  }
  const artifact = await register(sid, bytes, "release-brief.txt");
  const content = `/sessions/${sid}/artifacts/${artifact.id}/content`;
  assert.equal((await fetch(a + content, { headers })).status, 409);
  assert.equal(
    (await fetch(a + content, { method: "PUT", headers, body: bytes })).status,
    200,
  );
  assert.deepEqual(
    Buffer.from(await (await fetch(b + content, { headers })).arrayBuffer()),
    bytes,
  );
  const large = Buffer.alloc(2 * 1024 * 1024 + 37, 6),
    partial = await register(sid, large, "upload-recovery.bin"),
    path = `/sessions/${sid}/artifacts/${partial.id}/content`;
  assert.equal(
    (
      await fetch(a + path, {
        method: "PUT",
        headers,
        body: Readable.from([large.subarray(0, 1024 * 1024 + 99)]),
        duplex: "half",
      })
    ).status,
    409,
  );
  assert.equal((await fetch(b + path, { headers })).status, 409);
  assert.equal(
    (await fetch(b + path, { method: "PUT", headers, body: large })).status,
    200,
  );
  assert.equal(
    hash(Buffer.from(await (await fetch(a + path, { headers })).arrayBuffer())),
    hash(large),
  );
  pass(
    "S3 artifacts: pending refusal, interrupted streamed upload, exact retry and cross-API download",
  );
  const question = await create("Review the deployment checklist");
  await submit(
    question,
    plan("write_file", {
      path: `checklist-${key}.txt`,
      content: "Deployment checklist for review",
    }),
  );
  const waiting = await wait(question, "waiting");
  const consoleURL = `http://127.0.0.1:${m.ports.console}`,
    consoleKey = (
      await readFile(join(m.root, "private/console-key"), "utf8")
    ).trim();
  const login = await fetch(consoleURL + "/login", {
    method: "POST",
    headers: { Origin: consoleURL, "Content-Type": "application/json" },
    body: JSON.stringify({ key: consoleKey }),
  });
  assert.equal(login.status, 200);
  const cookie = login.headers.get("set-cookie").split(";")[0];
  const observer = (
    await readFile(join(m.root, "private/observer-token"), "utf8")
  ).trim();
  for (const port of [m.ports.apiA, m.ports.apiB]) {
    assert.equal(
      (
        await fetch(`http://127.0.0.1:${port}/sessions/${question}/controls`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${observer}`,
            "Content-Type": "application/json",
          },
          body: "{}",
        })
      ).status,
      403,
    );
  }
  await running.stop("api-a");
  const observed = await fetch(consoleURL + `/api/sessions/${question}`, {
    headers: { Cookie: cookie },
  });
  assert.equal(observed.status, 200);
  assert.equal(
    (await observed.json()).interactions[0].id,
    waiting.interactions[0].id,
  );
  assert.equal(
    (
      await fetch(consoleURL + "/api/sessions", {
        method: "POST",
        headers: { Cookie: cookie, "Content-Type": "application/json" },
        body: "{}",
      })
    ).status,
    405,
  );
  await running.start("api-a");
  pass(
    "Console authenticates, fails over reads, preserves pending decision and refuses mutations",
  );
  // PostgreSQL outage poisons old connections; explicit process replacement
  // reconciles durable identity rather than silently retrying mutations.
  await compose(m, ["stop", "postgres"]);
  assert.equal(
    (
      await fetch(a + `/sessions/${sid}`, {
        headers,
        signal: AbortSignal.timeout(15000),
      })
    ).status,
    503,
  );
  for (const role of ["worker-a", "worker-b", "api-a", "api-b"])
    await running.stop(role);
  await compose(m, ["up", "-d", "--wait", "postgres"]);
  for (const role of ["api-a", "api-b", "worker-a", "worker-b"])
    await running.start(role);
  await until(async () => {
    const s = await snap(question);
    return s.interactions[0]?.id === waiting.interactions[0].id;
  });
  assert.deepEqual(
    Buffer.from(await (await fetch(b + content, { headers })).arrayBuffer()),
    bytes,
  );
  await compose(m, ["restart", "objects"]);
  await until(async () => {
    const r = await fetch(b + content, { headers });
    return r.ok && Buffer.from(await r.arrayBuffer()).equals(bytes);
  });
  pass(
    "PostgreSQL and S3 restarts preserve session, approval and artifact bytes; explicit API/worker reconnect",
  );
  if (m.observability) {
    await compose(m, ["stop", "observability"]);
    const outage = await create("Collector outage · execution check", {
      mode: "bypass",
    });
    await submit(
      outage,
      plan("example_echo", {
        text: "Harness remains available without telemetry",
      }),
    );
    await wait(outage, "completed");
    const health = await request(a, "/readyz");
    assert.equal(health.status, "ready");
    await until(async () => {
      const h = await request(a, "/readyz");
      return h.telemetry.export_failures > 0;
    }, 20000);
    await compose(m, ["up", "-d", "--wait", "observability"]);
    const password = (
        await readFile(join(m.root, "private/grafana-password"), "utf8")
      ).trim(),
      g = `http://127.0.0.1:${m.ports.grafana}`;
    const gh = {
      Authorization:
        "Basic " + Buffer.from("admin:" + password).toString("base64"),
    };
    assert.equal((await fetch(g + "/api/search")).status, 401);
    assert.equal(
      (
        await fetch(g + "/api/dashboards/uid/openworker-harness", {
          headers: gh,
        })
      ).status,
      200,
    );
    await until(async () => {
      await request(a, "/readyz");
      const r = await fetch(
        g +
          "/api/datasources/proxy/uid/prometheus/api/v1/query?query=" +
          encodeURIComponent(
            'sum(openworker_operation_count_total{openworker_operation="tool.execute"})',
          ),
        { headers: gh },
      );
      const j = await r.json();
      return j.data?.result?.some((x) => +x.value[1] > 0);
    }, 60000);
    for (const [path, nonempty] of [
      [
        "/api/datasources/proxy/uid/tempo/api/search?q=" +
          encodeURIComponent('{ resource.service.name = "openworker" }'),
        (j) => j.traces?.length > 0,
      ],
      [
        "/api/datasources/proxy/uid/loki/loki/api/v1/query_range?query=" +
          encodeURIComponent('{service_name="openworker"}') +
          "&limit=1",
        (j) => j.data?.result?.length > 0,
      ],
    ])
      await until(async () => {
        const r = await fetch(g + path, { headers: gh });
        return r.ok && nonempty(await r.json());
      }, 60000);
    pass(
      "Collector outage does not block work; authenticated Grafana queries real metrics, traces and logs",
    );
  }
  checkpoint = {
    session_id: sid,
    waiting_session_id: question,
    interaction_id: waiting.interactions[0].id,
    artifact_id: artifact.id,
    artifact_sha256: hash(bytes),
    environment_id: env.id,
    scripted_model: true,
  };
} finally {
  await running.close();
}
await compose(m, ["down"]); // Deliberately no --volumes.
await up(m);
const replacement = await launch(m);
try {
  const pending = await snap(checkpoint.waiting_session_id);
  assert.equal(pending.interactions[0].id, checkpoint.interaction_id);
  const data = await fetch(
    `${b}/sessions/${checkpoint.session_id}/artifacts/${checkpoint.artifact_id}/content`,
    { headers },
  );
  assert.equal(data.status, 200);
  assert.equal(
    createHash("sha256")
      .update(Buffer.from(await data.arrayBuffer()))
      .digest("hex"),
    checkpoint.artifact_sha256,
  );
  pass(
    "Full Compose down/up preserves original approval identity and verified artifact bytes",
  );
  await writeFile(
    join(m.root, "qualification.json"),
    JSON.stringify(
      { ...checkpoint, time: new Date().toISOString(), checks: report },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "Qualification complete. Run stack.mjs run to inspect the retained examples.",
  );
} finally {
  await replacement.close();
}
