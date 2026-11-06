import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Harness } from "@openworker/harness";
import { modelFixture } from "./model-fixture.mjs";
test(
  "provider probe validates model identity, rejects failure, creates no agent/session",
  { timeout: 20000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "arcade-probe-")),
      fixture = await modelFixture();
    const h = await Harness.start({
      executable:
        process.env.OPENWORKER_RUNTIME ??
        resolve("../../target/release/openworker-harness"),
      stateDir: root,
      sandbox: "off",
    });
    t.after(async () => {
      await h.shutdown();
      await fixture.close();
      await rm(root, { recursive: true, force: true });
    });
    await assert.rejects(
      h.testModelProvider({ provider: "missing", name: "fixture" }),
    );
    assert.equal(fixture.state.requests.length, 0);
    await h.setModelProvider({
      name: "probe",
      api: "chat-completions",
      baseUrl: fixture.provider.baseUrl,
    });
    const before = await h.request("agent/list", {});
    await h.testModelProvider({ provider: "probe", name: "fixture" });
    assert.equal(fixture.state.requests.length, 1);
    assert.equal(fixture.state.requests[0].tools?.length ?? 0, 0);
    assert.deepEqual(await h.request("agent/list", {}), before);
    fixture.state.mode = "fail";
    await assert.rejects(
      h.testModelProvider({ provider: "probe", name: "fixture" }),
    );
    assert.equal(fixture.state.requests.length, 2);
  },
);
