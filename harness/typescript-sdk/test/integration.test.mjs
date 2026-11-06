import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { randomUUID } from "node:crypto";
import { Harness } from "../dist/index.js";

const executable = process.env.OW_TS_TEST_HOST;
assert.ok(
  executable,
  "Set OW_TS_TEST_HOST to a Linux sdk-test-host built with test-support",
);
const plan = (steps) => "TEST_RESOURCE_GRANTS " + JSON.stringify(steps);
const probe = (block) => ({ name: "sdk__probe", arguments: { block } });
const question = {
  name: "ask_user",
  arguments: { question: "Continue?", options: ["Yes", "No"] },
};
const common = {
  instructions: "Use the tools when asked.",
  tools: ["sdk.probe", "test.probe"],
};

async function recovered(h, pid) {
  for (let i = 0; i < 6000; i++) {
    if (h.state === "ready" && h.pid !== pid) return;
    await delay(20);
  }
  throw Error(`Host did not recover: ${h.state}`);
}
async function fileContains(path, text) {
  for (let i = 0; i < 500; i++) {
    try {
      if ((await readFile(path, "utf8")).includes(text)) return;
    } catch {}
    await delay(20);
  }
  throw Error("Expected tool effect missing");
}

for (const database of ["sqlite", "postgres"]) {
  test(
    `real Rust host: ${database} lifecycle, streaming, teams, MCP and recovery`,
    {
      skip: database === "postgres" && !process.env.OW_PG_TEST_URL,
      timeout: 360_000,
    },
    async (t) => {
      const root = await mkdtemp(join(tmpdir(), "ow-ts-real-"));
      const work = join(root, "work");
      await mkdir(work);
      const servers = join(root, "servers.json");
      await writeFile(
        servers,
        JSON.stringify({
          version: 1,
          servers: [
            {
              namespace: "example",
              transport: "stdio",
              program: process.execPath,
              args: [
                fileURLToPath(
                  new URL("../examples/echo-mcp.mjs", import.meta.url),
                ),
              ],
            },
          ],
        }),
      );
      const storage =
        database === "postgres"
          ? {
              type: "postgres",
              connectionEnv: "OW_PG_TEST_URL",
              schema: "ow_ts_" + randomUUID().replaceAll("-", ""),
              loopbackPlaintext: true,
            }
          : { type: "sqlite" };
      const options = {
        executable,
        stateDir: join(root, "state"),
        sandbox: "off",
        scripted: true,
        restart: { maxRestarts: 5, delayMs: 100 },
        storage,
        agents: [
          { ...common, id: "assistant" },
          {
            ...common,
            id: "automatic",
            tools: [...common.tools, "sdk.burst"],
            approvals: { mode: "only", tools: ["test.probe"] },
          },
          {
            ...common,
            id: "lead",
            planning: true,
            fallbackModel: "team-fixture",
            subagents: [
              {
                id: "worker",
                instructions: "Do assigned work.",
                tools: ["test.probe"],
                provider: "anthropic",
                fallbackModel: "team-fixture",
              },
            ],
          },
          {
            id: "mcp",
            instructions: "Echo the user text with your tool.",
            tools: ["example.echo"],
            servers,
          },
        ],
      };
      if (database === "postgres") {
        const config = join(root, "migrate.json");
        await writeFile(
          config,
          JSON.stringify({
            state_dir: options.stateDir,
            runner: executable,
            coworkers: [],
            sandbox: "off",
            storage: {
              kind: "postgres",
              connection_env: storage.connectionEnv,
              schema: storage.schema,
              loopback_plaintext: true,
            },
          }),
        );
        execFileSync(executable, ["sdk-test-migrate", config], {
          stdio: "pipe",
        });
      }
      let h = await Harness.start(options);
      console.log(`${database}: host ready`);
      t.after(async () => {
        await h.shutdown();
        await rm(root, { recursive: true, force: true });
      });

      // Memory credentials stay outside agent JSON; fixture validates both providers without real API calls.
      await h.setConnection({
        coworker: "assistant",
        id: "test",
        label: "Test",
        provider: "openai",
        apiKey: "fixture-only",
      });
      await h.setConnection({
        coworker: "worker",
        id: "test",
        label: "Test",
        provider: "anthropic",
        apiKey: "fixture-only",
      });
      const s = await h.session("assistant", { workspace: work });
      const first = await s.run(plan([probe(false)]), {
        submissionId: "first",
      });
      console.log(`${database}: approval parked; killing child`);
      assert.equal(first.status, "interrupted");
      assert.equal(first.interruptions[0].kind, "approval");
      await assert.rejects(readFile(join(work, "proof.txt")), {
        code: "ENOENT",
      });
      await assert.rejects(s.close());
      await assert.rejects(
        s.respond(first.interruptions[0], { answer: "Yes" }),
        { code: "InvalidInput" },
      );

      // Actual SIGKILL while an approval is parked. Never lose its durable identity.
      const pid = h.pid;
      process.kill(pid, "SIGKILL");
      await recovered(h, pid);
      console.log(`${database}: child recovered`);
      const pending = await s.resume({ submissionId: "first" });
      assert.equal(pending.interruptions[0].id, first.interruptions[0].id);
      await s.respond(pending.interruptions[0], { decision: "once" });
      await s.respond(pending.interruptions[0], { decision: "once" });
      await assert.rejects(
        s.respond(pending.interruptions[0], { decision: "deny" }),
      );
      const done = await s.resume({ submissionId: "first" });
      assert.equal(done.status, "completed");
      assert.equal(await readFile(join(work, "proof.txt"), "utf8"), "called\n");

      const q = await s.run(plan([question]), { submissionId: "question" });
      assert.equal(q.interruptions[0].kind, "question");
      const old = await s.run(plan([probe(false)]), { submissionId: "first" });
      assert.deepEqual(
        old,
        done,
        "old submission returns its recorded result, not the newer question",
      );
      await assert.rejects(s.run(plan([]), { submissionId: "first" }), {
        code: "Remote",
      });

      // App shutdown/relaunch as well as child-process restart.
      const ref = s.reference();
      await h.shutdown();
      h = await Harness.start(options);
      console.log(`${database}: app restarted`);
      const restored = await h.resume(JSON.parse(JSON.stringify(ref)));
      const again = await restored.resume({ submissionId: "question" });
      assert.equal(again.interruptions[0].id, q.interruptions[0].id);
      await restored.respond(again.interruptions[0], { answer: "Yes" });
      assert.equal((await restored.resume()).status, "completed");

      const sessionGrant = await restored.run(plan([probe(false)]));
      await restored.respond(sessionGrant.interruptions[0], {
        decision: "session",
      });
      assert.equal((await restored.resume()).status, "completed");
      const grantPid = h.pid;
      process.kill(grantPid, "SIGKILL");
      await recovered(h, grantPid);
      assert.equal(
        (await restored.run(plan([probe(false)]))).status,
        "completed",
        "session grant survives restart",
      );

      const automatic = await h.session("automatic", { workspace: work });
      const streamed = await automatic.runStreamed(plan([probe(false)]), {
        submissionId: "auto",
      });
      const events = [];
      for await (const e of streamed) events.push(e);
      assert.equal((await streamed.result()).status, "completed");
      assert.ok(
        events.some(
          (e) =>
            e.summary?.type === "auto_approved" &&
            e.event.data.origin === "app_policy",
        ),
      );
      assert.ok(events.some((e) => e.summary?.type === "message"));
      const replay = await automatic.resumeStreamed({
        submissionId: "auto",
        cursors: { [automatic.id]: 0 },
      });
      const replayed = [];
      for await (const e of replay) replayed.push(e.event.event_id);
      assert.equal(new Set(replayed).size, replayed.length);
      assert.ok(replayed.length >= events.length);

      // >4 MB of real durable progress, with a child crash between replay pages.
      assert.equal(
        (
          await automatic.run(
            plan(
              Array.from({ length: 10 }, (_, i) => ({
                name: "sdk__burst",
                arguments: { start: i * 55 },
              })),
            ),
            {
              submissionId: "burst",
            },
          )
        ).status,
        "completed",
      );
      const burst = await automatic.resumeStreamed({
        submissionId: "burst",
        cursors: { [automatic.id]: 0 },
      });
      const numbers = [];
      let killed = false;
      for await (const e of burst) {
        if (
          e.event.type === "tool_progress" &&
          e.event.data.qualified_name === "sdk.burst"
        ) {
          numbers.push(e.event.data.data.n);
          if (!killed && numbers.length === 130) {
            killed = true;
            process.kill(h.pid, "SIGKILL");
            // SDK's next safe journal read must reconnect without user intervention.
          }
        }
      }
      assert.deepEqual(
        numbers,
        Array.from({ length: 550 }, (_, n) => n),
      );
      assert.equal((await burst.result()).status, "completed");

      // Native tool cancellation, not merely aborting a UI wait.
      const before = (await readFile(join(work, "proof.txt"), "utf8")).length;
      const blockingStart = await h.request("run/start", {
        session_id: automatic.id,
        submission_id: "blocking",
        text: plan([probe(true)]),
      });
      let admitted = false;
      for (let n = 0; n < 300; n++) {
        if ((await readFile(join(work, "proof.txt"), "utf8")).length > before) {
          admitted = true;
          break;
        }
        await delay(20);
      }
      assert.ok(admitted, "cancel only after the tool writes its one effect");
      await automatic.cancel();
      const cancelled = await automatic.resume();
      if (database === "postgres") {
        // The server call ledger preserves admitted-but-unsettled effects.
        // Do not translate uncertainty into a clean cancellation in the SDK.
        assert.equal(cancelled.status, "failed");
        assert.equal(cancelled.failure, "interrupted");
        const journal = await h.request("event/read", {
          session_id: automatic.id,
          after: blockingStart.cursors[automatic.id],
        });
        assert.ok(
          journal.events.some(
            (e) =>
              e.type === "turn_completed" && e.data.outcome_unknown_count > 0,
          ),
        );
      } else assert.equal(cancelled.status, "cancelled");
      assert.equal(
        (await readFile(join(work, "proof.txt"), "utf8")).length,
        before + "called\n".length,
      );
      await automatic.close();

      const beforeApproval = await h.session("assistant", { workspace: work });
      assert.equal(
        (await beforeApproval.run(plan([probe(false)]))).status,
        "interrupted",
      );
      await beforeApproval.cancel();
      assert.equal((await beforeApproval.resume()).status, "cancelled");
      assert.equal(
        (await readFile(join(work, "proof.txt"), "utf8")).length,
        before + "called\n".length,
      );

      const teamSession = await h.session("lead", { workspace: work });
      const team = teamSession.team();
      assert.ok(team);
      const item = await team.createItem(
        { title: "Produce proof", criteria: "One durable proof" },
        "item-1",
      );
      assert.equal(
        (
          await team.createItem(
            { title: "Produce proof", criteria: "One durable proof" },
            "item-1",
          )
        ).id,
        item.id,
      );
      const worker = teamSession.reference().team.workers[0].session_id;
      const result = await teamSession.runWith(
        plan([
          {
            name: "team_assign",
            arguments: {
              item_id: item.id,
              expected_revision: item.revision,
              session_id: worker,
            },
          },
        ]),
        async (i) => {
          assert.equal(i.agent, "worker");
          return { decision: "once" };
        },
      );
      assert.equal(result.status, "completed");
      assert.equal(result.board.items[item.id].status, "done");
      await fileContains(join(work, "team-proof.txt"), "TEAM_PROOF");
      await team.postMessage("Evidence verified", { itemId: item.id });
      assert.equal((await team.board()).items[item.id].status, "done");
      assert.ok(await team.events());

      const mcp = await h.session("mcp", { workspace: work });
      const mcpResult = await mcp.runWith(
        plan([
          { name: "example__echo", arguments: { text: "hello from Node MCP" } },
        ]),
        (i) => {
          assert.equal(i.kind, "approval");
          return { decision: "once" };
        },
      );
      assert.equal(mcpResult.status, "completed");
      const mcpEvents = await h.request("event/read", {
        session_id: mcp.id,
        after: 0,
      });
      assert.ok(JSON.stringify(mcpEvents).includes("hello from Node MCP"));
      assert.ok(
        JSON.stringify(mcpEvents).includes("echoed"),
        "MCP structured result reached the real harness",
      );
    },
  );
}
