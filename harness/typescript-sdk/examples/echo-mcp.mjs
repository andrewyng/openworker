// Minimal Node.js MCP provider. Tools stay outside the Rust process.
// A real product should use its MCP SDK for richer transport/lifecycle support.
import { createInterface } from "node:readline";
const lines = createInterface({ input: process.stdin });
for await (const line of lines) {
  if (Buffer.byteLength(line) > 1024 * 1024) process.exit(1);
  const { id, method, params } = JSON.parse(line);
  if (id === undefined) continue;
  let result;
  if (method === "initialize")
    result = {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "example-echo", version: "1" },
    };
  else if (method === "tools/list")
    result = {
      tools: [
        {
          name: "echo",
          description:
            "Return the supplied text without accessing files or the network.",
          inputSchema: {
            type: "object",
            properties: { text: { type: "string", maxLength: 1000 } },
            required: ["text"],
            additionalProperties: false,
          },
        },
      ],
    };
  else if (
    method === "tools/call" &&
    params?.name === "echo" &&
    typeof params.arguments?.text === "string" &&
    params.arguments.text.length <= 1000
  ) {
    result = {
      content: [{ type: "text", text: params.arguments.text }],
      structuredContent: { echoed: params.arguments.text },
      isError: false,
    };
  } else {
    process.stdout.write(
      JSON.stringify({
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: "Unsupported request" },
      }) + "\n",
    );
    continue;
  }
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}
