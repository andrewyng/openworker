import { createServer } from "node:http";
export function makePdf(text) {
  const escaped = text.replace(/[\\()]/g, "\\$&");
  const stream = `BT /F1 12 Tf 30 750 Td (${escaped}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
  ];
  let out = "%PDF-1.4\n",
    offsets = [0];
  for (const [i, obj] of objects.entries()) {
    offsets.push(Buffer.byteLength(out));
    out += `${i + 1} 0 obj\n${obj}\nendobj\n`;
  }
  const xref = Buffer.byteLength(out);
  out +=
    `xref\n0 6\n0000000000 65535 f \n` +
    offsets
      .slice(1)
      .map((n) => `${String(n).padStart(10, "0")} 00000 n \n`)
      .join("") +
    `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(out);
}
export async function modelFixture() {
  const requests = [];
  const server = createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const request = JSON.parse(raw);
    requests.push(request);
    const files = (request.input ?? []).flatMap(item => item.content ?? [])
      .filter(part => part.type === "input_file").map(part =>
        Buffer.from((part.file_data ?? "").split(",").at(-1), "base64").toString("utf8"));
    const source = raw + files.join("\n");
    const text = source.includes("Orbit revenue")
      ? "Revenue is 42 credits. [p. 1]"
      : source.includes("Cedar budget")
        ? "The budget is 17 credits. [p. 1]"
        : "This codebase exports a greeting from src/main.ts.";
    const frame = (value) => `data: ${JSON.stringify(value)}\n\n`;
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write(
      frame({ type: "response.output_text.delta", delta: text.slice(0, 12) }),
    );
    setTimeout(() => {
      res.write(
        frame({ type: "response.output_text.delta", delta: text.slice(12) }),
      );
      res.end(
        frame({
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
            usage: { input_tokens: 50, output_tokens: 15 },
          },
        }),
      );
    }, 150);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    requests,
    url: `http://127.0.0.1:${server.address().port}/v1`,
    close: () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(resolve);
      }),
  };
}
