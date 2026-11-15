// Actual managed Rust process + deterministic streaming provider fixtures.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Harness, Attachment, guard, hook } from "../dist/index.js";
import { stageInput } from "../dist/attachments.js";
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aM9sAAAAASUVORK5CYII=",
  "base64",
);
const pdf = Buffer.from("%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF");
const image = () =>
  Attachment.fromBytes({
    name: "screen.png",
    mediaType: "image/png",
    data: png,
  });
const report = () =>
  Attachment.fromBytes({
    name: "report.pdf",
    mediaType: "application/pdf",
    data: pdf,
  });
const opts = { skip: !process.env.OW_TS_TEST_HOST, timeout: 120000 };
function frames(api) {
  const f = (v) => `data: ${JSON.stringify(v)}\n\n`;
  if (api === "responses")
    return (
      f({ type: "response.output_text.delta", delta: "Understood." }) +
      f({
        type: "response.completed",
        response: {
          status: "completed",
          output: [
            {
              type: "message",
              role: "assistant",
              content: [{ type: "output_text", text: "Understood." }],
            },
          ],
          usage: { input_tokens: 20, output_tokens: 4 },
        },
      })
    );
  if (api === "chat-completions")
    return (
      f({
        id: "fixture",
        choices: [
          {
            index: 0,
            delta: { content: "Understood." },
            finish_reason: "stop",
          },
        ],
      }) + "data: [DONE]\n\n"
    );
  return (
    f({
      type: "message_start",
      message: {
        role: "assistant",
        content: [],
        usage: { input_tokens: 20, output_tokens: 0 },
      },
    }) +
    f({
      type: "content_block_start",
      index: 0,
      content_block: { type: "text", text: "" },
    }) +
    f({
      type: "content_block_delta",
      index: 0,
      delta: { type: "text_delta", text: "Understood." },
    }) +
    f({ type: "content_block_stop", index: 0 }) +
    f({
      type: "message_delta",
      delta: { stop_reason: "end_turn" },
      usage: { output_tokens: 4 },
    }) +
    f({ type: "message_stop" })
  );
}
async function fixture(t, api, extra = {}) {
  const root = await mkdtemp(join(tmpdir(), "ow-inputs-"));
  let harness;
  const requests = [];
  const server = createServer(async (req, res) => {
    try {
      let raw = "";
      for await (const c of req) raw += c;
      requests.push(JSON.parse(raw));
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.end(frames(api));
    } catch (e) {
      res.writeHead(500);
      res.end();
    }
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  t.after(async () => {
    await harness?.shutdown();
    server.closeAllConnections();
    await new Promise((r) => server.close(r));
    await rm(root, { recursive: true, force: true });
  });
  async function start() {
    harness = await Harness.start({
      executable: process.env.OW_TS_TEST_HOST,
      stateDir: root,
      sandbox: "off",
      ...extra,
    });
    await harness.setModelProvider({
      name: "Fixture",
      api,
      baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
      credentials: { apiKey: "fixture-only", storage: "memory" },
    });
    return harness;
  }
  await start();
  const agent = () =>
    harness.createAgent({
      name: "reader",
      instructions: "Explain attached content.",
      tools: [],
      builtinTools: { askUser: false, readHistory: false },
      planning: false,
      memory: false,
      scheduling: false,
      defaultModel: { provider: "Fixture", name: "fixture" },
    });
  return {
    requests,
    root,
    agent,
    start,
    get harness() {
      return harness;
    },
  };
}
for (const api of ["responses", "chat-completions", "anthropic"])
  test(
    `${api}: ordered images, PDFs and text survive replay and restart`,
    opts,
    async (t) => {
      const f = await fixture(t, api);
      const session = await f.agent().newSession();
      const notes = Attachment.fromBytes({
        name: "notes.md",
        mediaType: "text/plain",
        data: Buffer.from("notes-unique-sentinel"),
      });
      const input = {
        content: [
          { type: "text", text: "First" },
          image(),
          { type: "text", text: "Second" },
          report(),
          notes,
          { type: "text", text: "Last" },
        ],
      };
      const stream = await session.runStreamed(input, {
        submissionId: "ordered",
      });
      const events = [];
      for await (const e of stream) events.push(e);
      assert.equal((await stream.result()).status, "completed");
      const messages = () =>
        api === "responses"
          ? f.requests.at(-1).input
          : f.requests.at(-1).messages;
      const blocks = messages().find((m) => m.role === "user").content;
      assert.deepEqual(
        blocks.map((p) => p.type),
        api === "responses"
          ? [
              "input_text",
              "input_image",
              "input_text",
              "input_file",
              "input_text",
              "input_text",
            ]
          : api === "anthropic"
            ? ["text", "image", "text", "document", "text", "text"]
            : ["text", "image_url", "text", "file", "text", "text"],
      );
      assert.equal(blocks[0].text, "First");
      assert.equal(blocks[2].text, "Second");
      assert.match(blocks[4].text, /notes.md\nnotes-unique-sentinel/);
      assert.equal(blocks[5].text, "Last");
      const data =
        api === "responses"
          ? blocks[3].file_data
          : api === "anthropic"
            ? blocks[3].source.data
            : blocks[3].file.file_data;
      assert.ok(data.endsWith(pdf.toString("base64")), "PDF bytes preserved");
      const snapshot = await f.harness.request("session/attach", {
        session_id: session.id,
      });
      const journal = JSON.stringify(events);
      const raw = JSON.stringify(snapshot);
      for (const serialized of [journal, raw]) {
        assert.ok(!serialized.includes(png.toString("base64")));
        assert.ok(!serialized.includes(pdf.toString("base64")));
        assert.ok(!serialized.includes("notes-unique-sentinel"));
      }
      assert.ok(raw.includes("input_attachment"));
      const before = f.requests.length;
      assert.equal(
        (await session.run(input, { submissionId: "ordered" })).status,
        "completed",
      );
      assert.equal(
        f.requests.length,
        before,
        "Identical retry does not call model",
      );
      await assert.rejects(() =>
        session.run(
          { content: [...input.content, { type: "text", text: "changed" }] },
          { submissionId: "ordered" },
        ),
      );
      const ref = session.reference();
      await f.harness.shutdown();
      await f.start();
      const resumed = await f.harness.resume(ref);
      assert.equal(
        (await resumed.run("Continue after restart.")).status,
        "completed",
      );
      assert.deepEqual(
        messages().find((m) => m.role === "user").content,
        blocks,
      );
      const foreign = await f.agent().newSession();
      const history = snapshot.history ?? snapshot.session?.history;
      const id = raw.match(/att_[a-f0-9]{64}/)?.[0];
      assert.ok(id);
      await assert.rejects(() =>
        foreign.run({
          content: [{ type: "image", source: { type: "attachment", id } }],
        }),
      );
      assert.equal(
        (await resumed.run("One attachment appended.", image())).status,
        "completed",
      );
    },
  );

test(
  "bounded chunks, sealed replay, failed staging and text guardrails",
  opts,
  async (t) => {
    const f = await fixture(t, "chat-completions");
    const session = await f.agent().newSession();
    const big = Buffer.from("bounded-chunk ".repeat(100000));
    const file = join(f.root, "large.txt");
    await writeFile(file, big);
    const a = await Attachment.fromFile(file);
    const run = await session.send({
      content: [{ type: "text", text: "Read" }, a],
    });
    assert.equal((await run.result()).status, "completed");
    assert.ok(JSON.stringify(f.requests.at(-1)).includes("bounded-chunk "));
    const bad = Attachment.fromBytes({
      name: "bad.pdf",
      mediaType: "application/pdf",
      data: Buffer.from("not a PDF"),
    });
    const count = f.requests.length;
    await assert.rejects(() => session.run("Read", bad));
    assert.equal(f.requests.length, count);
    const text = Attachment.fromBytes({
      name: "secret.txt",
      mediaType: "text/plain",
      data: Buffer.from("SECRET_12345"),
    });
    await assert.rejects(() =>
      session.run(
        { content: [text] },
        {
          guardrails: {
            userInput: [guard.regex({ name: "deny", pattern: "SECRET_" })],
          },
        },
      ),
    );
    assert.equal(f.requests.length, count);
    const otherText = Attachment.fromBytes({
      name: "other.txt",
      mediaType: "text/plain",
      data: Buffer.from("SECRET_12345"),
    });
    const safe = await session.run(
      { content: [text, otherText] },
      {
        guardrails: {
          userInput: [
            guard.regex({
              name: "hide",
              pattern: "SECRET_[0-9]+",
              action: "redact",
            }),
          ],
        },
      },
    );
    assert.equal(safe.status, "completed");
    assert.ok(
      !JSON.stringify(f.requests.at(-1).messages.at(-1)).includes(
        "SECRET_12345",
      ),
    );
    await assert.rejects(
      () =>
        session.run("Inspect", image(), {
          guardrails: {
            userInput: [guard.regex({ name: "required", pattern: "DENY" })],
          },
        }),
      /Binary attachments/,
    );
    const priorImage = await f.agent().newSession();
    assert.equal(
      (await priorImage.run("Inspect", image())).status,
      "completed",
    );
    const priorCount = f.requests.length;
    const blockedReplay = await priorImage.run("Inspect previous input again", {
      hooks: [
        hook.beforeModel({
          name: "required-model-input",
          run: () => ({ action: "allow" }),
        }),
      ],
    });
    assert.notEqual(blockedReplay.status, "completed");
    assert.equal(
      f.requests.length,
      priorCount,
      "A new model-input policy also covers binary history from earlier turns",
    );
    const aborted = new AbortController();
    aborted.abort();
    await assert.rejects(
      () => session.run("Stop", image(), { signal: aborted.signal }),
      (e) => e.code === "Aborted",
    );
  },
);

test("input validation is local and uploads never exceed the frame bound", async () => {
  const send = [];
  const req = async (m, p) => {
    send.push([m, p]);
    return {};
  };
  await stageInput(
    {
      content: [
        Attachment.fromBytes({
          name: "large.txt",
          mediaType: "text/plain",
          data: Buffer.alloc(1200000, 65),
        }),
      ],
    },
    "s",
    req,
  );
  assert.ok(send.length > 4);
  for (const [, p] of send)
    assert.ok(Buffer.byteLength(JSON.stringify(p)) < 400000);
  for (const input of [
    { content: [] },
    { content: [{ type: "text", text: "ok" }], text: "mixed" },
    {
      content: [
        {
          type: "document",
          source: { type: "base64", mediaType: "text/plain", data: "!!!" },
        },
      ],
    },
    { content: [{ type: "text", text: "x".repeat(32001) }] },
  ])
    await assert.rejects(() => stageInput(input, "s", req));
  assert.throws(() =>
    Attachment.fromBytes({
      name: "../escape",
      mediaType: "text/plain",
      data: Buffer.from("x"),
    }),
  );
  assert.throws(() =>
    Attachment.fromBytes({
      name: "bad.txt",
      mediaType: "text/plain",
      data: Buffer.from([0xff]),
    }),
  );
  assert.throws(() =>
    Attachment.fromBytes({
      name: "bad.zip",
      mediaType: "application/zip",
      data: Buffer.from("x"),
    }),
  );
});

test("cancelling during the final upload commit prevents turn submission", async () => {
  const controller = new AbortController();
  let commits = 0;
  await assert.rejects(
    () =>
      stageInput(
        { content: [image()] },
        "s",
        async (_m, p) => {
          if (p.upload.operation === "commit") {
            commits++;
            controller.abort();
          }
          return {};
        },
        controller.signal,
      ),
    (e) => e.code === "Aborted",
  );
  assert.equal(commits, 1);
});
