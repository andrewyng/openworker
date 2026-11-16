import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { start } from "../server/index.mjs";
const report = {
  title: "Fixture scratch",
  summary: "Deterministic test response, not a real AI assessment.",
  severity: "moderate",
  recommendation: "inspect",
  confidence: "limited_view",
  findings: [],
  nextChecks: ["Measure."],
  limitations: ["Fixture."],
  references: ["FIN-01"],
};
test("edge API authenticates, persists a photo, receives actual lifecycle events and records only human decisions", async () => {
  const dir = await mkdtemp(join(tmpdir(), "inspectionbuddy-api-"));
  let calls = 0;
  const agent = {
    health: async () => ({ connected: true, model: "test fixture" }),
    start: async (r, p, saved) => {
      calls++;
      assert.ok(p.bytes.length > 100);
      await saved({
        session: { sessionId: "s-test" },
        submissionId: "submission-test",
      });
      return {};
    },
    consume: async (r, h, event) => {
      await event({ type: "model_request", event_id: 1 });
      return { report, trace: null };
    },
    close() {},
  };
  const app = await start({
    mode: "edge",
    port: 0,
    dataDir: dir,
    appToken: "test-only-token",
    agentAdapter: agent,
  });
  const req = (p, method = "GET", body) =>
    fetch(app.url + "/api" + p, {
      method,
      headers: {
        authorization: "Bearer test-only-token",
        "content-type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  try {
    assert.equal((await fetch(app.url + "/api/inspections")).status, 401);
    assert.equal(
      (
        await req("/inspections", "POST", {
          sampleId: "nope",
          requestId: "abc",
        })
      ).status,
      400,
    );
    const payload = {
      sampleId: "door-scratch",
      requestId: "fixed-request",
      part: "Door panel",
      station: "Paint inspection",
    };
    const create = await req("/inspections", "POST", payload);
    assert.equal(create.status, 201);
    let r = await create.json();
    for (let i = 0; i < 60; i++) {
      r = await (await req("/inspections/" + r.id)).json();
      if (r.status !== "analyzing") break;
      await new Promise((ok) => setTimeout(ok, 20));
    }
    assert.equal(r.status, "review");
    assert.equal(r.report.title, "Fixture scratch");
    assert.equal(r.decision, null);
    assert.equal(calls, 1);
    assert.equal(
      (await req("/inspections/" + r.id + "/photo")).headers.get(
        "content-type",
      ),
      "image/png",
    );
    assert.equal((await req("/inspections", "POST", payload)).status, 200);
    assert.equal(calls, 1);
    assert.equal(
      (await req("/inspections", "POST", { ...payload, notes: "changed" }))
        .status,
      409,
    );
    assert.equal(
      (
        await req("/inspections/" + r.id + "/decision", "POST", {
          revision: r.revision - 1,
          inspector: "Sam",
          disposition: "pass",
        })
      ).status,
      409,
    );
    const saved = await req("/inspections/" + r.id + "/decision", "POST", {
      revision: r.revision,
      inspector: "Sam",
      disposition: "rework",
      note: "Checked by inspector.",
    });
    assert.equal(saved.status, 200);
    const reviewed = await saved.json();
    assert.equal(reviewed.decision.inspector, "Sam");
    assert.equal(reviewed.status, "reviewed");
    assert.equal(reviewed.report.title, "Fixture scratch");
  } finally {
    await app.close();
    await rm(dir, { recursive: true, force: true });
  }
});
test("upstream failures preserve an honest offline UI", async () => {
  const app = await start({
    mode: "frontend",
    port: 0,
    upstream: "http://127.0.0.1:1",
  });
  try {
    const r = await fetch(app.url + "/api/status");
    assert.equal(r.status, 503);
    assert.match((await r.json()).error, /not connected/);
  } finally {
    await app.close();
  }
});
test("workstation frontend rejects cross-origin writes and never exposes its upstream credential", async () => {
  const app = await start({
    mode: "frontend",
    port: 0,
    upstream: "http://127.0.0.1:1",
    appToken: "not-in-browser-12345",
  });
  try {
    const blocked = await fetch(app.url + "/api/inspections", {
      method: "POST",
      headers: {
        origin: "https://unrelated.example",
        "content-type": "application/json",
      },
      body: "{}",
    });
    assert.equal(blocked.status, 403);
    const js = await (await fetch(app.url + "/app.js")).text();
    assert.ok(!js.includes("not-in-browser-12345"));
  } finally {
    await app.close();
  }
});

test("local mode serves the complete app without a bearer token but enforces loopback and browser origin", async () => {
  await assert.rejects(start({ mode: "local", host: "0.0.0.0" }), /loopback/);
  const dir = await mkdtemp(join(tmpdir(), "inspectionbuddy-local-"));
  const app = await start({ mode: "local", port: 0, dataDir: dir,
    agentAdapter: { health: async () => ({ connected: true }), close() {} } });
  try {
    const status = await (await fetch(app.url + "/api/status")).json();
    assert.equal(status.role, "local-development");
    assert.equal(status.photos, "local-directory");
    assert.match(await (await fetch(app.url)).text(), /InspectionBuddy/);
    const response = await fetch(app.url + "/api/inspections", {
      method: "POST", headers: { origin: "https://other.example", "content-type": "application/json" }, body: "{}",
    });
    assert.equal(response.status, 403);
    const invalid = await fetch(app.url + "/api/inspections", { method: "POST", body: "{}" });
    assert.equal(invalid.status, 415);
  } finally { await app.close(); await rm(dir, { recursive: true, force: true }); }
});
