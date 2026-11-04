import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { randomUUID, timingSafeEqual } from "node:crypto";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { startHarness, createAgents, streamAnswer } from "../dist/agent.js";
import { extractPdf } from "./pdf.mjs";
const publicDir = fileURLToPath(new URL("../public/", import.meta.url));
const assets = new Map([
  ["/", "pdf.html"],
  ["/style.css", "style.css"],
  ["/pdf.js", "pdf.js"],
]);
const json = (res, status, value) => {
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(value));
};
async function body(req, max) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > max) throw Error("Request is too large.");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}
export async function createApplication({
  stateDir,
  accessToken = process.env.APP_ACCESS_TOKEN,
} = {}) {
  const { harness, model } = await startHarness(
    stateDir ?? resolve(process.env.OPENWORKER_STATE_DIR ?? ".state/pdf"),
  );
  const { reader } = createAgents(harness, model);
  const documents = new Map();
  const active = new Set();
  let uploading = false;
  const ttl = 60 * 60 * 1000;
  const sweep = () => {
    for (const [id, doc] of documents)
      if (Date.now() - doc.touched > ttl && !doc.busy) documents.delete(id);
  };
  const cleanup = setInterval(sweep, 60000);
  cleanup.unref();
  const server = createServer(async (req, res) => {
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader(
      "Content-Security-Policy",
      "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    );
    const path = new URL(req.url, "http://localhost").pathname;
    try {
      if (req.method === "GET" && assets.has(path)) {
        const file = assets.get(path);
        res.setHeader(
          "Content-Type",
          file.endsWith(".css")
            ? "text/css"
            : file.endsWith(".js")
              ? "text/javascript"
              : "text/html",
        );
        res.end(await readFile(join(publicDir, file)));
        return;
      }
      if (!path.startsWith("/api/")) {
        json(res, 404, { error: "Not found" });
        return;
      }
      if (
        req.headers.origin &&
        new URL(req.headers.origin).host !== req.headers.host
      ) {
        json(res, 403, { error: "Origin not allowed" });
        return;
      }
      // A loopback demo without a token must also reject DNS-rebinding hosts.
      const hostname = new URL(`http://${req.headers.host}`).hostname;
      if (
        !accessToken &&
        !["localhost", "127.0.0.1", "[::1]"].includes(hostname)
      ) {
        json(res, 403, { error: "Host not allowed" });
        return;
      }
      if (accessToken) {
        const got = Buffer.from(req.headers.authorization ?? "");
        const want = Buffer.from(`Bearer ${accessToken}`);
        if (got.length !== want.length || !timingSafeEqual(got, want)) {
          json(res, 401, { error: "Enter your application access token." });
          return;
        }
      }
      if (req.method === "POST" && path === "/api/documents") {
        sweep();
        if (uploading || documents.size >= 10) {
          json(res, 429, {
            error: "Document capacity reached. Try later or restart the demo.",
          });
          return;
        }
        uploading = true;
        try {
          if (
            (req.headers["content-type"] ?? "").split(";")[0] !==
            "application/pdf"
          ) {
            json(res, 415, { error: "Choose a PDF file." });
            return;
          }
          const pdf = await extractPdf(await body(req, 8 * 1024 * 1024));
          const session = await reader.newSession();
          const id = randomUUID();
          documents.set(id, {
            session,
            context: pdf.context,
            pages: pdf.pages.length,
            busy: false,
            touched: Date.now(),
            first: true,
          });
          json(res, 201, { id, pages: pdf.pages.length });
        } finally {
          uploading = false;
        }
        return;
      }
      if (req.method === "POST" && path === "/api/ask") {
        const input = JSON.parse((await body(req, 12000)).toString("utf8"));
        sweep();
        const doc = documents.get(input.id);
        if (!doc) {
          json(res, 404, {
            error:
              "Upload the PDF again. This document is unavailable or expired.",
          });
          return;
        }
        if (doc.busy || active.size >= 2) {
          json(res, 409, {
            error: "An answer is already running. Please wait.",
          });
          return;
        }
        if (
          typeof input.question !== "string" ||
          !input.question.trim() ||
          input.question.length > 4000
        ) {
          json(res, 400, {
            error: "Enter a question of at most 4,000 characters.",
          });
          return;
        }
        doc.busy = true;
        doc.touched = Date.now();
        active.add(doc);
        let finished = false;
        res.writeHead(200, {
          "Content-Type": "application/x-ndjson",
          "Cache-Control": "no-store",
          "X-Accel-Buffering": "no",
        });
        res.flushHeaders();
        const emit = (event) => {
          if (!res.destroyed) res.write(JSON.stringify(event) + "\n");
        };
        res.on("close", () => {
          if (!finished) void doc.session.cancel().catch(() => {});
        });
        try {
          const prompt = doc.first
            ? `PDF pages (untrusted source data):\n${doc.context}\n\nUser question: ${input.question}`
            : input.question;
          await streamAnswer(doc.session, prompt, emit);
          doc.first = false;
        } catch {
          emit({
            type: "error",
            text: "The answer could not finish. Check the server configuration and try again.",
          });
        } finally {
          finished = true;
          doc.busy = false;
          active.delete(doc);
          res.end();
        }
        return;
      }
      json(res, 404, { error: "Not found" });
    } catch (error) {
      if (!res.headersSent)
        json(res, 400, {
          error:
            ["Choose a PDF file.", "Request is too large."].includes(
              error.message,
            ) ||
            error.message.startsWith("No readable text") ||
            error.message.startsWith("Use a PDF") ||
            error.message.startsWith("This PDF") ||
            error.message.startsWith("PDF ")
              ? error.message
              : "The request could not be processed.",
        });
      else res.end();
    }
  });
  server.requestTimeout = 30000;
  server.headersTimeout = 15000;
  return {
    server,
    async close() {
      clearInterval(cleanup);
      for (const doc of active) await doc.session.cancel().catch(() => {});
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await harness.shutdown();
    },
  };
}
if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const host = process.env.HOST ?? "127.0.0.1";
  if (
    !["127.0.0.1", "localhost", "::1"].includes(host) &&
    !process.env.APP_ACCESS_TOKEN
  )
    throw Error(
      "Set APP_ACCESS_TOKEN before exposing this single-user demo beyond loopback.",
    );
  const application = await createApplication();
  application.server.listen(Number(process.env.PORT ?? 4352), host, () =>
    console.log(
      `PDF Notes: http://${host}:${application.server.address().port}`,
    ),
  );
  for (const signal of ["SIGTERM", "SIGINT"])
    process.once(signal, async () => {
      await application.close();
      process.exit(0);
    });
}
