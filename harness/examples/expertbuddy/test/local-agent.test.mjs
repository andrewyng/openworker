import { test } from "node:test";
import assert from "node:assert/strict";
import { LocalAgent } from "../server/local-agent.mjs";

test("local recovery streams the saved receipt/cursor without submitting a new turn", async () => {
  const adapter = new LocalAgent({});
  let submissions = 0, wait;
  const session = { id: "saved-session", send() { submissions++; },
    async resumeStreamed(options) {
      wait = options;
      return (async function* () { yield { event: { event_id: 43, turn_id: "original-turn" } }; })();
    },
    resume: async options => ({ status: "completed", submissionId: options.submissionId }),
  };
  adapter.connect = async () => ({ resume: async ref => {
    assert.equal(ref.session_id, "saved-session"); return session;
  } });
  const handle = await adapter.resume({ cursor: 42, service: {
    session: { session_id: "saved-session" }, submissionId: "original-submission",
  } });
  const events = [];
  for await (const event of handle.run.events()) events.push(event);
  assert.equal(events.length, 1);
  assert.deepEqual(wait, { submissionId: "original-submission", cursors: { "saved-session": 42 } });
  assert.equal((await handle.run.result()).submissionId, "original-submission");
  assert.equal(submissions, 0);
  await assert.rejects(adapter.resume({}), /not be resubmitted/);
});
