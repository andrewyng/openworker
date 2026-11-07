import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startHarness, createAgents, streamAnswer } from "../dist/agent.js";
import { createApplication } from "../web/server.mjs";
import { makePdf, modelFixture } from "./fixtures.mjs";
test(
  "actual Rust SDK host streams summaries and isolated PDF answers through the web API",
  { skip: !process.env.OPENWORKER_EXECUTABLE, timeout: 120000 },
  async (t) => {
    const fixture = await modelFixture();
    const root = await mkdtemp(join(tmpdir(), "ow-tracks-"));
    let app, h;
    const saved = { ...process.env };
    Object.assign(process.env, {
      OPENWORKER_MODEL: "fixture",
      OPENWORKER_MODEL_URL: fixture.url,
      OPENAI_API_KEY: "fixture-only",
    });
    delete process.env.OPENWORKER_PROVIDER;
    t.after(async () => {
      await app?.close();
      await h?.shutdown();
      await fixture.close();
      await rm(root, { recursive: true, force: true });
      for (const key of [
        "OPENWORKER_MODEL",
        "OPENWORKER_MODEL_URL",
        "OPENAI_API_KEY",
        "OPENWORKER_PROVIDER",
      ]) {
        if (saved[key] === undefined) delete process.env[key];
        else process.env[key] = saved[key];
      }
    });
    const runtime = await startHarness(join(root, "desktop"));
    h = runtime.harness;
    const session = await createAgents(
      h,
      runtime.model,
    ).summarizer.newSession();
    const events = [];
    await streamAnswer(
      session,
      'Summarize src/main.ts: export const greeting = "hello";',
      (event) => events.push(event),
    );
    assert.ok(events.some((e) => e.type === "delta"));
    assert.match(events.at(-1).text, /src\/main.ts/);
    app = await createApplication({
      stateDir: join(root, "web"),
      accessToken: "test-access",
    });
    await new Promise((resolve) => app.server.listen(0, "127.0.0.1", resolve));
    const url = `http://127.0.0.1:${app.server.address().port}`;
    assert.equal(
      (await fetch(url + "/api/documents", { method: "POST" })).status,
      401,
    );
    assert.equal(
      (
        await fetch(url + "/api/documents", {
          method: "POST",
          headers: {
            Authorization: "Bearer test-access",
            Origin: "http://attacker.invalid",
          },
        })
      ).status,
      403,
    );
    const upload = async (text) => {
      const r = await fetch(url + "/api/documents", {
        method: "POST",
        headers: {
          Authorization: "Bearer test-access",
          "Content-Type": "application/pdf",
        },
        body: makePdf(text),
      });
      assert.equal(r.status, 201);
      return r.json();
    };
    const orbit = await upload("Orbit revenue is 42 credits.");
    const cedar = await upload("Cedar budget is 17 credits.");
    assert.notEqual(orbit.id, cedar.id);
    const ask = async (id, question) => {
      const r = await fetch(url + "/api/ask", {
        method: "POST",
        headers: {
          Authorization: "Bearer test-access",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ id, question }),
      });
      assert.equal(r.status, 200);
      const events = (await r.text()).trim().split("\n").map(JSON.parse);
      assert.ok(events.some((e) => e.type === "delta"));
      assert.equal(events.at(-1).type, "done");
      return events.at(-1).text;
    };
    assert.match(
      await ask(orbit.id, "What is the revenue?"),
      /42 credits.*\[p\. 1\]/,
    );
    assert.match(
      await ask(cedar.id, "What is the budget?"),
      /17 credits.*\[p\. 1\]/,
    );
    assert.match(await ask(orbit.id, "Repeat the revenue."), /42 credits/);
    assert.ok(
      !JSON.stringify(fixture.requests[2]).includes("Orbit revenue"),
      "PDF sessions must not share document content",
    );
    for (const request of fixture.requests)
      assert.deepEqual(request.tools ?? [], []);
    const unknown = await fetch(url + "/api/ask", {
      method: "POST",
      headers: {
        Authorization: "Bearer test-access",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ id: "missing", question: "Hi" }),
    });
    assert.equal(unknown.status, 404);
  },
);
