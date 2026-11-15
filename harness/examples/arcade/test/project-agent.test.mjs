import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { AgentService } from "../build/electron/agent-service.js";
import { modelFixture, until } from "./model-fixture.mjs";
import { providerId } from "../build/electron/agent-service.js";
import { StarterCatalog } from "../build/electron/starters.js";

test(
  "real SDK catalog callbacks stage assets and restart at approval without changing the published revision",
  { timeout: 60000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "arcade-catalog-agent-")),
      l = new Library(root),
      s = new SettingsStore(root),
      f = await modelFixture();
    f.state.mode = "catalog";
    const options = {
      executable:
        process.env.OPENWORKER_RUNTIME ??
        resolve("../../target/release/openworker-harness"),
      emit() {},
    };
    let a = new AgentService(l, s, options);
    t.after(async () => {
      await a.close();
      l.close();
      await f.close();
      await rm(root, { recursive: true, force: true });
    });
    await a.configure(f.provider);
    const game = new StarterCatalog().remix(
        "orbit-runner",
        l,
        s.get().gamesRoot,
      ),
      before = l.get(game.id).revisionId;
    await a.send(game.id, "Use Pip the robot");
    await until(
      () => a.view(game.id).pending.length,
      "catalog publish approval",
    );
    assert.ok(l.draft(game.id).files["assets/library/pip-robot.glb"]);
    assert.equal(l.project(game.id)["assets/library/pip-robot.glb"], undefined);
    assert.equal(l.get(game.id).revisionId, before);
    const pending = a.view(game.id).pending[0];
    await a.close();
    a = new AgentService(l, s, options);
    await a.open(game.id);
    await a.respond(game.id, pending.id, "once");
    await until(() => !a.view(game.id).active, "catalog publication");
    assert.ok(l.project(game.id)["assets/library/pip-robot.LICENSE.txt"]);
    assert.ok(l.project(game.id)["assets/library/pip-robot.glb"]);
    assert.deepEqual(f.state.errors, []);
  },
);

test(
  "a bundled remix gets a fresh SDK session and approval without changing its original",
  { timeout: 60000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "arcade-remix-agent-"));
    const l = new Library(root),
      s = new SettingsStore(root),
      catalog = new StarterCatalog(),
      f = await modelFixture();
    f.state.mode = "project";
    const a = new AgentService(l, s, {
      executable:
        process.env.OPENWORKER_RUNTIME ??
        resolve("../../target/release/openworker-harness"),
      emit() {},
    });
    t.after(async () => {
      await a.close();
      l.close();
      await f.close();
      await rm(root, { recursive: true, force: true });
    });
    const starter = catalog.list()[0],
      host = new URL(starter.previewUrl).host;
    const original = catalog.asset(host, "index.html");
    const game = catalog.remix(starter.id, l, s.get().gamesRoot);
    assert.equal(game.session, null);
    await a.configure(f.provider);
    await a.open(game.id);
    assert.equal(l.get(game.id).session, null);
    await a.send(game.id, "Make this game my own");
    await until(
      () => a.view(game.id).pending.length,
      "remix publication approval",
    );
    assert.equal(l.get(game.id).session.agent, "arcade-studio-builder-v7");
    assert.equal(l.revisions(game.id).length, 1);
    assert.equal(l.project(game.id)["index.html"], original);
    await a.respond(game.id, a.view(game.id).pending[0].id, "once");
    await until(() => !a.view(game.id).active, "remix publication");
    assert.equal(l.get(game.id).status, "ready");
    assert.equal(l.revisions(game.id).length, 2);
    assert.match(l.project(game.id)["LICENSE.txt"], /MIT License/);
    assert.equal(
      JSON.parse(l.project(game.id)["starter-origin.json"]).id,
      starter.id,
    );
    assert.equal(catalog.asset(host, "index.html"), original);
    assert.deepEqual(f.state.errors, []);
  },
);
for (const legacyName of [
  "arcade-studio-builder-v1",
  "arcade-studio-builder-v2",
  "arcade-studio-builder-v3",
  "arcade-studio-builder-v4",
  "arcade-studio-builder-v5",
  "arcade-studio-builder-v6",
])
  test(
    `${legacyName} session continues after restart without changing its tool catalog`,
    { timeout: 60000 },
    async (t) => {
      const root = await mkdtemp(join(tmpdir(), "arcade-legacy-agent-")),
        l = new Library(root),
        s = new SettingsStore(root),
        f = await modelFixture();
      const options = {
        executable:
          process.env.OPENWORKER_RUNTIME ??
          resolve("../../target/release/openworker-harness"),
        emit() {},
      };
      let a = new AgentService(l, s, options);
      t.after(async () => {
        await a.close();
        l.close();
        await f.close();
        await rm(root, { recursive: true, force: true });
      });
      await a.configure(f.provider);
      const model = {
          provider: providerId(f.provider),
          name: f.provider.model,
        },
        g = l.create(
          s.get().gamesRoot,
          "Older game",
          {
            idea: "Keep my snake",
            kind: "Arcade",
            vibe: "Neon",
            difficulty: "Easy",
          },
          model,
        );
      // Arrange an earlier-agent session using the real SDK host; never mutate SDK tables.
      const old = await a.harness.session(legacyName, {
        workspace: g.directory,
        model,
      });
      l.attach(g.id, old.reference());
      await a.send(g.id, "Build");
      await until(() => a.view(g.id).pending.length, "legacy approval");
      await a.respond(g.id, a.view(g.id).pending[0].id, "session");
      await until(() => !a.view(g.id).active, "legacy completion");
      if (legacyName.endsWith("v1"))
        assert.ok(
          f.state.requests[0].tools.every(
            (tool) => !tool.function.name.startsWith("project__"),
          ),
        );
      if (!legacyName.endsWith("v6"))
        assert.ok(
          f.state.requests[0].tools.every(
            (tool) => tool.function.name !== "preview__playtest",
          ),
        );
      await a.close();
      a = new AgentService(l, s, options);
      await a.open(g.id);
      await a.send(g.id, "Add stars");
      await until(() => !a.view(g.id).active, "legacy resumed edit");
      if(l.get(g.id).status!=="ready")console.log(JSON.stringify(await a.harness.request("event/read",{session_id:l.get(g.id).session.session_id,after:0})).slice(-6000));
      assert.equal(l.get(g.id).status, "ready");
      assert.equal(l.revisions(g.id).length, 2);
      assert.equal(l.get(g.id).session.agent, legacyName);
    },
  );
test(
  "SDK stages files, restarts at project approval, then publishes complete snapshot",
  { timeout: 60000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "arcade-project-agent-")),
      l = new Library(root),
      s = new SettingsStore(root),
      f = await modelFixture();
    f.state.mode = "project";
    const options = {
      executable:
        process.env.OPENWORKER_RUNTIME ??
        resolve("../../target/release/openworker-harness"),
      emit() {},
    };
    let a = new AgentService(l, s, options);
    t.after(async () => {
      await a.close();
      l.close();
      await f.close();
      await rm(root, { recursive: true, force: true });
    });
    await a.configure(f.provider);
    let v = await a.create({
      idea: "A multi-file game",
      kind: "Puzzle",
      vibe: "Retro",
      difficulty: "Easy",
      engine: "canvas",
    });
    const id = v.game.id;
    await until(() => a.view(id).pending.length, "project approval");
    assert.equal(l.get(id).revisionId, null);
    assert.match(l.draft(id).files["src/game.js"], /multi-file/);
    assert.equal(a.view(id).pending[0].tool, "project__publish");
    const count = f.state.requests.length;
    await a.close();
    a = new AgentService(l, s, options);
    v = await a.open(id);
    assert.equal(v.pending.length, 1);
    assert.equal(f.state.requests.length, count);
    await a.respond(id, v.pending[0].id, "once");
    await until(() => !a.view(id).active, "published");
    assert.equal(l.get(id).status, "ready");
    assert.deepEqual(Object.keys(l.project(id)).sort(), [
      "index.html",
      "src/game.js",
      "styles/game.css",
    ]);
    assert.equal(l.revisions(id).length, 1);
    assert.deepEqual(f.state.errors, []);
  },
);
