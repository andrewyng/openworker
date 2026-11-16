import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm, readdir, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { Harness } from "../dist/index.js";

const options = { skip: !process.env.OW_TS_TEST_HOST, timeout: 60000 };
const access = (value, seconds = 900) => ({
  accessToken: value,
  expiresAt: Math.floor(Date.now() / 1000) + seconds,
});
const old = "synthetic-old-access-secret";
const fresh = "synthetic-new-access-secret";
async function setup(t, mode, getToken) {
  const root = await mkdtemp(join(tmpdir(), "ow-provider-auth-"));
  const seen = [];
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  const server = createServer(async (req, res) => {
    for await (const _ of req) {
      /* drain body */
    }
    seen.push(req.headers.authorization);
    if (
      mode === "401" ||
      mode === "403" ||
      (mode === "rotate" && req.headers.authorization === `Bearer ${old}`)
    ) {
      res.writeHead(mode === "403" ? 403 : 401, {
        "Content-Type": "application/json",
      });
      res.end(JSON.stringify({ error: { code: "authentication_error" } }));
      return;
    }
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write(
      'data: {"id":"test","choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":null}]}\n\n',
    );
    if (mode === "hold") await gate;
    if (mode === "stream-error") {
      res.end('data: {"error":{"code":"authentication_error"}}\n\n');
      return;
    }
    res.end(
      'data: {"id":"test","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
    );
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const h = await Harness.start({
    executable: process.env.OW_TS_TEST_HOST,
    stateDir: root,
    sandbox: "off",
  });
  t.after(async () => {
    release();
    await h.shutdown();
    server.closeAllConnections();
    await new Promise((r) => server.close(r));
    await rm(root, { recursive: true, force: true });
  });
  await h.setModelProvider({
    name: "ExampleApp",
    api: "chat-completions",
    baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
    auth: { getToken },
  });
  await h.registerTools({
    namespace: "app",
    tools: {
      noop: {
        description: "Unused fixture",
        inputSchema: { type: "object", properties: {} },
        execute: async () => {
          throw new Error("Tool must not execute");
        },
      },
    },
  });
  const agent = h.createAgent({
    name: "helper",
    instructions: "Say hello",
    tools: ["app.noop"],
    defaultModel: { provider: "ExampleApp", name: "fixture" },
  });
  const run = async (session) => (await session.send("Hello")).result();
  return { h, agent, seen, root, run, release };
}
test(
  "proactive rotation does not interrupt an accepted stream",
  options,
  async (t) => {
    const f = await setup(t, "hold");
    await f.h.updateProviderCredentials("ExampleApp", access(old));
    const s = await f.agent.newSession();
    const run = await s.send("Hello");
    const iterator = run.events()[Symbol.asyncIterator]();
    while (true) {
      const next = await iterator.next();
      assert.equal(next.done, false);
      if (next.value.event.type === "assistant_delta") break;
    }
    await f.h.updateProviderCredentials("ExampleApp", access(fresh));
    f.release();
    await iterator.return();
    assert.equal((await run.result()).status, "completed");
    assert.equal((await f.run(s)).status, "completed");
    assert.deepEqual(f.seen, [`Bearer ${old}`, `Bearer ${fresh}`]);
  },
);
test(
  "proactive replacement preserves session and secrets stay out of durable files",
  options,
  async (t) => {
    const f = await setup(t, "ok");
    await f.h.updateProviderCredentials("ExampleApp", access(old));
    const s = await f.agent.newSession();
    assert.equal((await f.run(s)).status, "completed");
    await f.h.updateProviderCredentials("ExampleApp", access(fresh));
    assert.equal((await f.run(s)).status, "completed");
    assert.deepEqual(f.seen, [`Bearer ${old}`, `Bearer ${fresh}`]);
    await assert.rejects(
      f.h.updateProviderCredentials("ExampleApp", access("expired-token", -1)),
    );
    // Flush durable state and release Windows' exclusive owner/database handles
    // before inspecting every file; do not skip locked files from the check.
    await f.h.shutdown();
    async function inspect(dir) {
      for (const item of await readdir(dir, { withFileTypes: true })) {
        const path = join(dir, item.name);
        if (item.isDirectory()) await inspect(path);
        else if (item.isFile()) {
          const data = await readFile(path);
          assert.equal(data.includes(Buffer.from(old)), false, path);
          assert.equal(data.includes(Buffer.from(fresh)), false, path);
        }
      }
    }
    await inspect(f.root);
  },
);
test(
  "HTTP 401 refreshes and retries once on the same run",
  options,
  async (t) => {
    const reasons = [];
    const f = await setup(t, "rotate", async (c) => {
      reasons.push(c.reason);
      return access(fresh);
    });
    await f.h.updateProviderCredentials("ExampleApp", access(old));
    const s = await f.agent.newSession();
    assert.equal((await f.run(s)).status, "completed");
    assert.deepEqual(reasons, ["rejected"]);
    assert.deepEqual(f.seen, [`Bearer ${old}`, `Bearer ${fresh}`]);
  },
);
for (const mode of ["401", "403", "stream-error"])
  test(
    `${mode}: bounded retry, no replay of streamed output`,
    options,
    async (t) => {
      let calls = 0;
      const f = await setup(t, mode, async () => {
        calls++;
        return access(fresh);
      });
      await f.h.updateProviderCredentials("ExampleApp", access(old));
      assert.equal((await f.run(await f.agent.newSession())).status, "failed");
      assert.equal(calls, mode === "401" ? 1 : 0);
      assert.equal(f.seen.length, mode === "401" ? 2 : 1);
    },
  );
test("near-expiry token refreshes before the request", options, async (t) => {
  const reasons = [];
  const f = await setup(t, "ok", async (c) => {
    reasons.push(c.reason);
    return access(fresh);
  });
  await f.h.updateProviderCredentials("ExampleApp", access(old, 30));
  assert.equal((await f.run(await f.agent.newSession())).status, "completed");
  assert.deepEqual(reasons, ["expiring"]);
  assert.deepEqual(f.seen, [`Bearer ${fresh}`]);
});
test(
  "concurrent sessions share a refresh and callback rebinds after child restart",
  options,
  async (t) => {
    let calls = 0;
    const f = await setup(t, "ok", async () => {
      calls++;
      await delay(50);
      return access(fresh);
    });
    const sessions = await Promise.all([
      f.agent.newSession(),
      f.agent.newSession(),
    ]);
    const results = await Promise.all(sessions.map(f.run));
    assert.ok(results.every((r) => r.status === "completed"));
    assert.equal(calls, 1);
    const pid = f.h.pid;
    process.kill(pid, "SIGKILL");
    for (let n = 0; n < 200; n++) {
      if (f.h.state === "ready" && f.h.pid !== pid) break;
      await delay(50);
    }
    assert.notEqual(f.h.pid, pid);
    assert.equal(f.h.state, "ready");
    assert.equal((await f.run(sessions[0])).status, "completed");
    assert.equal(calls, 2);
  },
);
test(
  "proactive update unblocks a pending callback and wins the race",
  options,
  async (t) => {
    let started;
    const called = new Promise((r) => {
      started = r;
    });
    const f = await setup(t, "ok", async ({ signal }) => {
      started();
      await new Promise((_, reject) =>
        signal.addEventListener("abort", () => reject(new Error("cancelled")), {
          once: true,
        }),
      );
      return access(old);
    });
    const s = await f.agent.newSession();
    const result = f.run(s);
    await called;
    await f.h.updateProviderCredentials("ExampleApp", access(fresh));
    assert.equal((await result).status, "completed");
    assert.deepEqual(f.seen, [`Bearer ${fresh}`]);
  },
);
test(
  "failed refresh sends no unauthenticated request; proactive recovery keeps session usable",
  options,
  async (t) => {
    const f = await setup(t, "ok", async () => {
      throw new Error("private-refresh-credential");
    });
    const s = await f.agent.newSession();
    const failed = await f.run(s);
    assert.equal(failed.status, "failed");
    assert.equal(
      JSON.stringify(failed).includes("private-refresh-credential"),
      false,
    );
    assert.equal(f.seen.length, 0);
    await f.h.updateProviderCredentials("ExampleApp", access(fresh));
    assert.equal((await f.run(s)).status, "completed");
  },
);

test(
  "cancelling a run aborts its pending authentication callback",
  options,
  async (t) => {
    let started, stopped;
    const called = new Promise((r) => {
      started = r;
    });
    const aborted = new Promise((r) => {
      stopped = r;
    });
    const f = await setup(t, "ok", async ({ signal }) => {
      started();
      await new Promise((_, reject) =>
        signal.addEventListener(
          "abort",
          () => {
            stopped();
            reject(new Error("cancelled"));
          },
          { once: true },
        ),
      );
      return access(fresh);
    });
    const s = await f.agent.newSession();
    const run = await s.send("Hello");
    await called;
    await s.cancel();
    await Promise.race([
      aborted,
      delay(5000).then(() => {
        throw new Error("Callback not aborted");
      }),
    ]);
    assert.equal((await run.result()).status, "cancelled");
    assert.equal(f.seen.length, 0);
  },
);
