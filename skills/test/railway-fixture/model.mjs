import { createServer } from "node:http";
let completed = false;
let requests = 0;
let finish;
createServer(async (req, res) => {
  if (req.url === "/status") {
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ completed, requests }));
    return;
  }
  if (req.url === "/release") {
    finish?.();
    finish = undefined;
    res.end("released");
    return;
  }
  let raw = "";
  for await (const chunk of req) raw += chunk;
  const body = JSON.parse(raw);
  if (req.url !== "/v1/responses" || body.model !== "fixture") {
    res.writeHead(400);
    res.end();
    return;
  }
  completed = false;
  requests++;
  const input = JSON.stringify(body.input);
  const text = input.includes("SECOND_MESSAGE")
    ? input.includes("FIRST_MESSAGE")
      ? "history-restored"
      : "history-missing"
    : "first-done";
  res.writeHead(200, { "content-type": "text/event-stream" });
  res.write(
    "data: " +
      JSON.stringify({ type: "response.output_text.delta", delta: text }) +
      "\n\n",
  );
  // The probe releases completion only after receiving a real streamed chunk.
  // This avoids timing assumptions and fails if the application buffers output.
  finish = () => {
    completed = true;
    res.end(
      "data: " +
        JSON.stringify({
          type: "response.completed",
          response: {
            status: "completed",
            output: [
              {
                type: "message",
                role: "assistant",
                content: [{ type: "output_text", text }],
              },
            ],
            usage: { input_tokens: 20, output_tokens: 8 },
          },
        }) +
        "\n\n",
    );
  };
}).listen(8081, "0.0.0.0");
