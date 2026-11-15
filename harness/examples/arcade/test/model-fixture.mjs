import { createServer } from "node:http";
import { setTimeout as delay } from "node:timers/promises";
export const gameHtml = (version = 1) =>
  `<!doctype html><html><head><style>body{background:#202b38;color:#fff;font:20px system-ui;display:grid;place-content:center;text-align:center;height:100vh;margin:0}button{font:inherit;border:0;border-radius:20px;background:#c4f3b1;padding:20px;cursor:pointer}h1{font-size:42px}p{color:#aebcca}</style></head><body><h1>Star Catcher</h1><p>Version ${version} · Catch a little joy</p><button id="play">Catch a star · 0</button><script>let n=0;document.querySelector('button').onclick=()=>document.querySelector('button').textContent='Catch a star · '+(++n)</script></body></html>`;
export async function modelFixture() {
  const state = {
    requests: [],
    errors: [],
    version: 0,
    mode: "normal",
    delay: 500,
    reads: [],
  };
  const server = createServer(async (req, res) => {
    try {
      let raw = "";
      for await (const part of req) raw += part;
      const body = JSON.parse(raw);
      state.requests.push(body);
      if (state.mode === "unauthorized") {
        res.writeHead(401);
        res.end("{}");
        return;
      }
      if (state.mode === "fail") {
        res.writeHead(400);
        res.end("{}");
        return;
      }
      const id = `fixture-${state.requests.length}`;
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.flushHeaders();
      const send = (delta, finish_reason = null) =>
        res.write(
          `data: ${JSON.stringify({ id, choices: [{ index: 0, delta, finish_reason }] })}\n\n`,
        );
      const finish = (reason) => {
        send({}, reason);
        res.write(
          `data: ${JSON.stringify({ id, choices: [{ index: 0, delta: {}, finish_reason: reason }], usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 } })}\n\n`,
        );
        res.end("data: [DONE]\n\n");
      };
      if (!body.tools?.length) {
        send({ content: "OK" });
        finish("stop");
        return;
      }
      const messages = body.messages,
        lastUser = messages.findLastIndex((m) => m.role === "user"),
        recent = messages.slice(lastUser + 1);
      let context = {};
      try {
        context = JSON.parse(messages[lastUser].content);
      } catch {}
      const outputs = recent.filter((m) => m.role === "tool"),
        calls = recent.flatMap((m) => m.tool_calls ?? []);
      const called = (name) => calls.some((c) => c.function.name === name);
      const tool = (name, args) => {
        send({
          tool_calls: [
            {
              index: 0,
              id: `call-${state.requests.length}`,
              type: "function",
              function: { name, arguments: JSON.stringify(args) },
            },
          ],
        });
        finish("tool_calls");
      };
      if (state.mode === "preview") {
        const count = calls.filter(
          (c) => c.function.name === "preview__playtest",
        ).length;
        if (count === 0) tool("preview__playtest", { action: "open" });
        else if (count === 1)
          tool("preview__playtest", {
            action: "keys",
            keys: ["ArrowRight"],
            durationMs: 100,
          });
        else if (count === 2) tool("preview__playtest", { action: "close" });
        else {
          send({
            content:
              "Preview inspection completed; publication was not requested.",
          });
          finish("stop");
        }
        return;
      }
      if (
        state.mode === "project" ||
        state.mode === "catalog" ||
        state.mode === "review"
      ) {
        if (state.mode === "catalog" && !called("assets__list")) {
          tool("assets__list", {});
          return;
        }
        if (state.mode === "catalog" && !called("assets__add")) {
          tool("assets__add", { id: "pip-robot" });
          return;
        }
        if (!called("project__list_files")) {
          tool("project__list_files", {});
          return;
        }
        if (!called("project__write_file")) {
          tool("project__write_file", {
            path: state.mode === "review" ? "index.html" : "src/game.js",
            content:
              state.mode === "review"
                ? gameHtml(++state.version)
                : "document.querySelector('h1').textContent='A multi-file game';",
          });
          return;
        }
        if (!called("project__publish")) {
          if (state.mode === "review" && !called("preview__playtest")) {
            tool("preview__playtest", { action: "open" });
            return;
          }
          const raw = outputs.at(-1).content;
          const last = JSON.parse(Array.isArray(raw) ? raw[0].text : raw);
          const data =
            last.structuredContent ?? JSON.parse(last.content[0].text);
          if (state.mode === "review" && !called("testing__report")) {
            tool("testing__report", {
              digest: data.digest,
              checks: {
                start: {
                  status: "passed",
                  note: "Title rendered in the fixture preview.",
                },
                movement: { status: "not_tested", note: "Not exercised." },
                pause: {
                  status: "failed",
                  note: "Fixture-reported pause issue.",
                },
                compact: { status: "not_tested", note: "Not assessed." },
                level: { status: "not_tested", note: "Not completed." },
              },
            });
            return;
          }
          tool("project__publish", {
            title: "Multi-file game",
            digest: data.digest,
          });
          return;
        }
        send({ content: "All files were published together." });
        finish("stop");
        return;
      }
      if (state.mode === "question" && !called("ask_user")) {
        tool("ask_user", {
          question: "Which color should the stars be?",
          options: ["Gold", "Blue"],
        });
        return;
      }
      if (context.arcade_context?.editing && !called("game__read_current")) {
        tool("game__read_current", {});
        return;
      }
      if (called("game__read_current"))
        state.reads.push(
          outputs.find(
            (m) =>
              calls.find((c) => c.id === m.tool_call_id)?.function.name ===
              "game__read_current",
          )?.content,
        );
      if (!called("game__publish")) {
        send({ content: "I’m shaping your game " });
        if (state.mode === "hold") {
          const timer = setTimeout(() => res.end(), 30000);
          res.on("close", () => clearTimeout(timer));
          return;
        }
        await delay(state.delay);
        if (res.destroyed) return;
        send({ content: "with a playful star-catching challenge." });
        tool("game__publish", {
          title: "Star Catcher",
          html: gameHtml(++state.version),
        });
        return;
      }
      send({
        content:
          "Your game is ready to play. Catch stars and try for a new high score!",
      });
      finish("stop");
    } catch (e) {
      state.errors.push(String(e));
      res.destroy();
    }
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return {
    state,
    provider: {
      name: "Fixture",
      preset: "custom",
      api: "chat-completions",
      model: "fixture-game",
      baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
    },
    async close() {
      server.closeAllConnections();
      await new Promise((r) => server.close(r));
    },
  };
}
export async function until(fn, message, timeout = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const result = await fn();
    if (result) return result;
    await delay(50);
  }
  throw new Error(`Timed out: ${message}`);
}
