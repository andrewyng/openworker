// Deterministic loopback fixture for hands-on native Tauri qualification.
// Not an LLM: it tests the real UI, SDK, policy, callbacks and WebView together.
import { createServer } from "node:http";
let requests = 0,
  blockedRequests = 0;
const server = createServer(async (req, res) => {
  if (req.url === "/stats") {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ requests, blockedRequests }));
    return;
  }
  if (req.url !== "/v1/chat/completions") {
    blockedRequests++;
    res.writeHead(403);
    res.end();
    return;
  }
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 1048576) {
      res.writeHead(413);
      res.end();
      return;
    }
  }
  const input = JSON.parse(raw);
  requests++;
  const lastUser = input.messages.findLastIndex((m) => m.role === "user");
  const finished = input.messages
    .slice(lastUser + 1)
    .some((m) => m.role === "tool");
  const html = `<!doctype html><meta charset="utf-8"><style>
  *{box-sizing:border-box}body{margin:0;background:#151329;color:#f8f5ff;font:18px system-ui;min-height:100vh;display:grid;place-content:center;text-align:center;padding:28px}h1{font-size:48px;letter-spacing:-2px;margin:0 0 12px}p{color:#c5bfde}button{background:#b8a7ff;color:#211936;border:0;border-radius:18px;font:600 22px system-ui;padding:24px 32px;cursor:pointer}small{display:block;margin-top:36px;color:#9e97bd;font-size:12px;line-height:1.8}#proof{white-space:pre-line}#status{min-height:28px}</style>
  <h1>Tiny Arcade</h1><p>A little game. A real native app.</p><button id="score">Score a point · 0</button><p id="status">Tap to start</p><small>Local integration fixture — not AI-generated</small><small id="proof">Checking preview isolation…</small>
  <script>
  let count=0;document.getElementById('score').onclick=()=>{document.getElementById('score').textContent='Score a point · '+(++count);document.getElementById('status').textContent=count>=3?'Nice! The native preview is interactive.':'Keep going!'};
  const proof={native:typeof window.__TAURI_INTERNALS__==='undefined',node:typeof require==='undefined',parent:false,network:false};
  try{void parent.document.body}catch{proof.parent=true}
  function show(){document.getElementById('proof').textContent='Native bridge: '+(proof.native?'absent':'EXPOSED')+' · Node: '+(proof.node?'absent':'EXPOSED')+'\\nParent access: '+(proof.parent?'blocked':'EXPOSED')+' · Network: '+(proof.network?'blocked':'pending')}
  show();fetch('http://127.0.0.1:${server.address().port}/forbidden').catch(()=>{proof.network=true;show()});
  </script>`;
  const delta = finished
    ? {
        content:
          "Your game is ready. This local fixture verified the app integration.",
      }
    : {
        tool_calls: [
          {
            index: 0,
            id: `preview-${requests}`,
            type: "function",
            function: {
              name: input.tools[0].function.name,
              arguments: JSON.stringify({ html }),
            },
          },
        ],
      };
  res.writeHead(200, { "Content-Type": "text/event-stream" });
  res.end(
    `data: ${JSON.stringify({ id: `fixture-${requests}`, choices: [{ index: 0, delta, finish_reason: null }] })}\n\ndata: ${JSON.stringify({ id: `fixture-${requests}`, choices: [{ index: 0, delta: {}, finish_reason: finished ? "stop" : "tool_calls" }] })}\n\ndata: [DONE]\n\n`,
  );
  console.log(
    JSON.stringify({
      request: requests,
      phase: finished ? "completion" : "tool-request",
    }),
  );
});
server.listen(0, "127.0.0.1", () =>
  console.log(
    JSON.stringify({
      baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
      model: "fixture-game",
      credentials: "none",
    }),
  ),
);
