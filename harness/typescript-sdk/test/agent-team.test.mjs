import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness } from "../dist/index.js";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
const plan = (steps) => "TEST_RESOURCE_GRANTS " + JSON.stringify(steps);
for(const backend of ["sqlite","postgres"]) test(
  `managed TypeScript named team on ${backend}: streaming, history and restart`,
  { skip: !process.env.OW_TS_TEST_HOST || (backend==="postgres"&&!process.env.OW_PG_TEST_URL), timeout: 120000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "ow-team-ts-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    await mkdir(join(root, "work"));
    const storage=backend==="postgres"?{type:"postgres",connectionEnv:"OW_PG_TEST_URL",schema:"ow_ts_team_"+randomUUID().replaceAll("-",""),loopbackPlaintext:true}:{type:"sqlite"};
    if(backend==="postgres") {
      const migration=join(root,"migration.json");
      await writeFile(migration,JSON.stringify({state_dir:join(root,"state"),runner:process.env.OW_TS_TEST_HOST,coworkers:[],agents:[],sandbox:"off",storage:{kind:"postgres",connection_env:"OW_PG_TEST_URL",schema:storage.schema,loopback_plaintext:true}}));
      execFileSync(process.env.OW_TS_TEST_HOST,["sdk-test-migrate",migration]);
    }
    const options = {
      storage,
      executable: process.env.OW_TS_TEST_HOST,
      stateDir: join(root, "state"),
      sandbox: "off",
      scripted: true,
      agents: ["lead", "builder", "verifier"].map((id) => ({
        id,
        instructions: "Coordinate named instances and report evidence.",
        planning: true,
        approvals: { mode: "never" },
      })),
    };
    let harness = await Harness.start(options);
    t.after(() => harness.shutdown());
    const team = await harness.newTeam("lead", {
      workspace: join(root, "work"),
      agentTypes: ["builder", "verifier"],
    });
    const task = plan([
      {
        name: "team_spawn",
        arguments: {
          agent_type: "builder",
          agent_id: "builder-one",
          message: plan([
            { name: "team_post_board", arguments: { message: "@lead built" } },
          ]),
        },
      },
      {
        name: "team_wait_agent",
        arguments: { agent_ids: ["builder-one"], timeout_ms: 10000 },
      },
      {
        name: "team_spawn",
        arguments: {
          agent_type: "builder",
          agent_id: "builder-two",
          message: plan([
            {
              name: "team_post_board",
              arguments: { message: "@lead second instance built" },
            },
          ]),
        },
      },
      {
        name: "team_wait_agent",
        arguments: { agent_ids: ["builder-two"], timeout_ms: 10000 },
      },
    ]);
    const run = await team.lead.send(task);
    const events = [];
    for await (const event of run.events()) events.push(event);
    const result = await run.result();
    assert.equal(result.status, "completed",JSON.stringify({result,tail:events.slice(-8)}));
    const snapshot = await team.snapshot();
    assert.equal(Object.keys(snapshot.roster.instances).length, 3);
    assert.equal((await team.history()).messages.length, 2);
    const posted = await team.post("same-request", "Passive note");
    assert.deepEqual(posted, await team.post("same-request", "Passive note"));
    assert.equal((await team.history()).messages.length, 3);
    await assert.rejects(team.post("same-request", "Changed"));
    await assert.rejects(
      team.sendMessage("private", "builder-one", "Disabled by policy"),
    );
    const reference = team.reference();
    await harness.shutdown();
    harness = await Harness.start(options);
    const resumed = await harness.resumeTeam(reference);
    assert.equal(resumed.id, team.id);
    assert.equal((await resumed.history()).messages.length, 3);
    assert.ok(events.length > 0);
  },
);
