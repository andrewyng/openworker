import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { tmpdir } from "node:os";
import { ToolImageResult } from "@openworker/harness";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { AgentService } from "../build/electron/agent-service.js";
import { StarterCatalog } from "../build/electron/starters.js";
import { modelFixture, until } from "./model-fixture.mjs";
const png =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=";
test(
  "SDK callbacks deliver ephemeral preview pixels to the model, not replay events",
  { timeout: 60000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "arcade-preview-agent-")),
      l = new Library(root),
      s = new SettingsStore(root),
      f = await modelFixture(),
      calls = [];
    f.state.mode = "preview";
    const a = new AgentService(l, s, {
      executable:
        process.env.OPENWORKER_RUNTIME ??
        resolve(
          `../../target/release/openworker-harness${process.platform === "win32" ? ".exe" : ""}`,
        ),
      emit() {},
      playtester: {
        async run(owner, game, args, signal) {
          assert.equal(signal.aborted, false);
          calls.push({ owner, game, args });
          return args.action === "close"
            ? { closed: true }
            : new ToolImageResult(
                { digest: "draft", action: args.action },
                { mimeType: "image/png", base64: png },
              );
        },
        close() {},
      },
    });
    t.after(async () => {
      await a.close();
      l.close();
      await f.close();
      await rm(root, { recursive: true, force: true });
    });
    await a.configure(f.provider);
    const g = new StarterCatalog().remix("orbit-runner", l, s.get().gamesRoot),
      revision = l.get(g.id).revisionId;
    await a.send(g.id, "Inspect and play the draft. Do not publish.");
    await until(() => !a.view(g.id).active, "preview run");
    assert.equal(a.view(g.id).pending.length, 0);
    assert.deepEqual(
      calls.map((c) => c.args.action),
      ["open", "keys", "close"],
    );
    assert.ok(
      calls.every(
        (c) => c.game === g.id && c.owner === l.get(g.id).session.session_id,
      ),
    );
    assert.equal(l.get(g.id).revisionId, revision);
    const images = f.state.requests.flatMap((r) =>
      r.messages.flatMap((m) =>
        Array.isArray(m.content)
          ? m.content.filter((c) => c.type === "image_url")
          : [],
      ),
    );
    assert.ok(
      images.length >= 2,
      "model receives actual image blocks, not encoded JSON text",
    );
    assert.ok(
      images.every((i) => i.image_url.url === `data:image/png;base64,${png}`),
    );
    const session = a.sessions.get(g.id),
      events = JSON.stringify(
        await a.harness.request("event/read", {
          session_id: session.id,
          after: 0,
        }),
      );
    assert.ok(events.includes("ephemeral_image"));
    assert.ok(!events.includes(png));
    assert.ok(!events.includes("data:image"));
    const history = JSON.stringify(
      await a.harness.request("history/read", {
        session_id: session.id,
        after: 0,
      }),
    );
    assert.ok(!history.includes(png));
    assert.ok(!history.includes("data:image"));
    assert.deepEqual(f.state.errors, []);
  },
);
