// Synthetic control and inference server. Each test gets a separate path prefix.
import { createServer } from "node:http";
import { pathToFileURL } from "node:url";
export function mockIma() {
  const cases = new Map();
  return createServer(async (req, res) => {
    try {
      const url = new URL(req.url, "http://localhost");
      const [, id, , method] = url.pathname.split("/");
      const rows = cases.get(id) ?? [];
      cases.set(id, rows);
      if (req.method === "GET") {
        res.end(JSON.stringify(rows));
        return;
      }
      if (req.method === "DELETE") {
        rows.push({ cancel: true });
        res.writeHead(204).end();
        return;
      }
      let data = "";
      for await (const part of req) {
        data += part;
        if (data.length > 500000) throw Error();
      }
      const b = JSON.parse(data),
        state = b.x_agent?.agent_status;
      rows.push({
        state,
        id: b.x_agent?.agent_session_id,
        request: b.x_request_id,
        route: b.x_route,
        query: url.searchParams.get("api-version"),
        header: req.headers["x-example"],
        option: b.sampling_hint,
        method,
      });
      if (state && state !== "start") {
        if (id.startsWith("outage")) {
          res.writeHead(503).end();
          return;
        }
        res.end(
          JSON.stringify({
            id: "status-" + b.x_request_id,
            choices: [],
            usage: { total_tokens: 0 },
          }),
        );
        return;
      }
      res.writeHead(200, { "content-type": "text/event-stream" });
      if (!id.startsWith("missing"))
        res.write(
          ": ima-route " +
            JSON.stringify({ target: "fixture", locus: "local" }) +
            "\n\n",
        );
      if (state) res.write(': ima-lifecycle {"agent_type":"main_agent"}\n\n');
      if (id.startsWith("cancel")) {
        res.flushHeaders();
        return;
      }
      const frame = (x) => res.write("data: " + JSON.stringify(x) + "\n\n");
      if (id.startsWith("conflict")) {
        frame({
          id: "m",
          choices: [
            { index: 0, delta: { content: "partial" }, finish_reason: null },
          ],
        });
        await new Promise((resolve) => setTimeout(resolve, 50));
        res.end(': ima-route {"locus":"remote","target":"fixture"}\n\n');
        return;
      }
      if (method === "messages") {
        frame({
          type: "message_start",
          message: {
            id: "m",
            role: "assistant",
            content: [],
            usage: { input_tokens: 4, output_tokens: 0 },
          },
        });
        frame({
          type: "content_block_start",
          index: 0,
          content_block: { type: "text", text: "" },
        });
        frame({
          type: "content_block_delta",
          index: 0,
          delta: { type: "text_delta", text: "Done" },
        });
        frame({ type: "content_block_stop", index: 0 });
        frame({
          type: "message_delta",
          delta: { stop_reason: "end_turn" },
          usage: { output_tokens: 1 },
        });
        frame({ type: "message_stop" });
        res.end();
        return;
      }
      const chunk = (delta, finish_reason = null) =>
        frame({ id: "m", choices: [{ index: 0, delta, finish_reason }] });
      const question = id.startsWith("question"),
        approval =
          id.startsWith("approve") ||
          id.startsWith("deny") ||
          id.startsWith("outage");
      if (
        (question || approval) &&
        !b.messages.some((m) => m.role === "tool")
      ) {
        const name = question
          ? "ask_user"
          : b.tools.find((t) => t.function.name.includes("probe")).function
              .name;
        chunk({
          tool_calls: [
            {
              index: 0,
              id: "call1",
              type: "function",
              function: {
                name,
                arguments: question
                  ? '{"question":"Which folder?","options":["A","B"]}'
                  : "{}",
              },
            },
          ],
        });
        chunk({}, "tool_calls");
      } else {
        chunk({ content: "Done" });
        chunk({}, "stop");
      }
      res.end("data: [DONE]\n\n");
    } catch {
      res.destroy();
    }
  });
}
if (
  process.argv[1] === "-" ||
  (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
)
  mockIma().listen(Number(process.env.PORT ?? 8080), "0.0.0.0");
