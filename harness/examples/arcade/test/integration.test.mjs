import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { AgentService } from "../build/electron/agent-service.js";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { modelFixture, until } from "./model-fixture.mjs";
const executable =
  process.env.OPENWORKER_RUNTIME ??
  resolve("../../target/release/openworker-harness");
const brief = {
  idea: "Catch the stars",
  kind: "Arcade",
  vibe: "Neon",
  difficulty: "Relaxed",
};
test(
  "real SDK: probe, streaming, approval restart, revisions, recovery, cancel and keys",
  { timeout: 120000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "arcade-sdk-")),
      fixture = await modelFixture(),
      library = new Library(root),
      settings = new SettingsStore(root),
      events = [];
    let service;
    const launch = () =>
      (service = new AgentService(library, settings, {
        executable,
        emit: (e) => events.push(e),
        credentialStorage: "memory",
      }));
    launch();
    t.after(async () => {
      await service.close();
      library.close();
      await fixture.close();
      await rm(root, { recursive: true, force: true });
    });
    await service.configure(fixture.provider, "fixture-secret-never-save");
    await service.testProvider();
    assert.equal(fixture.state.requests.length, 1);
    assert.equal(fixture.state.requests[0].tools?.length ?? 0, 0);
    assert.equal(library.list().length, 0);
    fixture.state.mode = "unauthorized";
    await assert.rejects(service.testProvider());
    fixture.state.mode = "normal";
    fixture.state.delay = 1800;
    let view = await service.create(brief),
      id = view.game.id;
    await until(
      () =>
        events.some(
          (e) => e.type === "project" && e.view.partial.includes("I’m shaping"),
        ),
      "partial text before completion",
    );
    assert.equal(library.get(id).revisionId, null);
    await until(() => service.view(id).pending.length, "publish approval");
    assert.equal(library.revisions(id).length, 0);
    assert.equal(service.view(id).partial, "");
    assert.ok(
      service
        .view(id)
        .messages.some(
          (m) => m.role === "assistant" && m.text.includes("shaping"),
        ),
    );
    const requestCount = fixture.state.requests.length;
    await service.close();
    launch();
    await service.configure(fixture.provider, "fixture-secret-never-save");
    view = await service.open(id);
    assert.equal(view.pending.length, 1);
    assert.equal(fixture.state.requests.length, requestCount);
    await assert.rejects(service.respond(id, "stale-interaction", "once"));
    await service.respond(id, view.pending[0].id, "once");
    await until(
      () => !service.view(id).active && library.get(id).status === "ready",
      "completed game",
    );
    assert.equal(library.revisions(id).length, 1);
    assert.match(library.html(id), /Version 1/);
    const first = library.get(id).revisionId;
    fixture.state.delay = 100;
    await service.send(id, "Add another star");
    await until(() => service.view(id).pending.length, "edit approval");
    await service.respond(id, service.view(id).pending[0].id, "session");
    await until(
      () => library.revisions(id).length === 2 && !service.view(id).active,
      "edited game",
    );
    assert.ok(fixture.state.reads.some((s) => s?.includes("Version 1")));
    await service.restore(id, first);
    assert.match(library.html(id), /Version 1/);
    await service.send(id, "Change the restored game");
    await until(
      () => library.revisions(id).length === 3 && !service.view(id).active,
      "session approval reused",
    );
    assert.ok(fixture.state.reads.at(-1).includes("Version 1"));
    const current = library.html(id);
    fixture.state.mode = "fail";
    await service.send(id, "Try changing it");
    await until(() => !service.view(id).active, "failed model");
    assert.equal(library.html(id), current);
    assert.equal(library.get(id).status, "failed");
    fixture.state.mode = "hold";
    await service.send(id, "Start a slow build");
    await until(() => service.view(id).partial, "slow streaming");
    await service.cancel(id);
    await until(() => !service.view(id).active, "cancel");
    assert.equal(library.get(id).status, "cancelled");
    assert.equal(library.html(id), current);
    fixture.state.mode = "normal";
    const count = fixture.state.requests.length;
    await service.close();
    launch();
    view = await service.open(id);
    assert.ok(view.previewUrl);
    assert.equal(fixture.state.requests.length, count);
    assert.ok(view.messages.length > 2);
    // Another game on screen never changes the destination of the active callback.
    await service.configure(
      { ...fixture.provider, model: "another-model" },
      "fixture-secret-never-save",
    );
    const second = library.duplicate(id, settings.get().gamesRoot);
    fixture.state.mode = "question";
    await service.send(id, "Ask me which color I prefer");
    await until(() => service.view(id).pending.length, "user question");
    assert.equal(service.view(id).pending[0].kind, "question");
    await service.open(second.id);
    await assert.rejects(
      service.respond(
        second.id,
        service.view(id).pending[0].id,
        undefined,
        "Gold",
      ),
    );
    await service.respond(
      id,
      service.view(id).pending[0].id,
      undefined,
      "Gold",
    );
    await until(() => !service.view(id).active, "question response");
    assert.equal(library.revisions(second.id).length, 1);
    assert.equal(library.get(second.id).session, null);
    assert.equal(library.get(id).model.name, "fixture-game");
    assert.equal(fixture.state.requests.at(-1).model, "fixture-game");
    fixture.state.mode = "normal";
    const newGame = await service.create(brief, "A second build");
    await until(
      () => service.view(newGame.game.id).pending.length,
      "new model approval",
    );
    assert.equal(library.get(newGame.game.id).model.name, "another-model");
    await service.respond(
      newGame.game.id,
      service.view(newGame.game.id).pending[0].id,
      "deny",
    );
    await until(() => !service.view(newGame.game.id).active, "denied publish");
    assert.equal(library.revisions(newGame.game.id).length, 0);
    assert.equal(
      (await readFile(join(root, "settings.json"), "utf8")).includes(
        "fixture-secret",
      ),
      false,
    );
    assert.equal(
      (await readFile(join(root, "library.sqlite"))).includes(
        Buffer.from("fixture-secret"),
      ),
      false,
    );
    assert.deepEqual(fixture.state.errors, []);
  },
);
