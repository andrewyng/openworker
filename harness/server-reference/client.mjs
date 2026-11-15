// Node 20+ reference client. No provider key, worker address or sandbox transport.
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline/promises';
const args = process.argv.slice(2);
const option = (name) => { const i = args.indexOf(name); return i < 0 ? undefined : args[i + 1]; };
const bases = (process.env.OW_API_URLS || 'http://127.0.0.1:8081,http://127.0.0.1:8082').split(',');
for (const base of bases) {
  const url = new URL(base);
  if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw Error('Reference client expects explicit loopback HTTP(S) APIs');
}
if (!process.env.OW_API_TOKEN_FILE) throw Error('Set OW_API_TOKEN_FILE; do not put tokens in arguments');
const token = (await readFile(process.env.OW_API_TOKEN_FILE, 'utf8')).trim();
const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
let node = 0;
async function request(path, body) {
  // Every mutation here has a stable request ID. Retrying the same receipt is
  // not retrying a tool call. Never manufacture another ID after response loss.
  let failure;
  for (let n = 0; n < bases.length; n++) {
    try {
      const response = await fetch(bases[node % bases.length] + path, { headers, method: body ? 'POST' : 'GET', body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(15_000) });
      const result = await response.json();
      if (!response.ok) {
        if (response.status < 500) throw Object.assign(Error(JSON.stringify(result)), { permanent: true });
        throw Error(JSON.stringify(result));
      }
      return result;
    } catch (e) { if (e.permanent) throw e; failure = e; node++; }
  }
  throw failure;
}
const key = option('--request-id') || randomUUID();
console.log(`Request ID: ${key} (reuse this ID if restarting after response loss)`);
let sid = option('--session');
if (!sid) {
  const environment = option('--environment');
  if (!environment) throw Error('Pass --environment ENV_ID or --session SESSION_ID');
  const created = await request('/sessions', { request_id: `create-${key}`, parameters: { name: 'Reference session', coworker: option('--coworker') || 'assistant-example', workspace: '/sandbox', environment_id: environment } });
  sid = created.session_id;
}
console.log(`Session: ${sid}`);
const fixture = args.includes('--fixture');
const text = fixture ? 'TEST_RESOURCE_GRANTS [{"name":"write_file","arguments":{"path":"hello.txt","content":"Hello from a remote OpenWorker environment."}}]' : option('--text');
let target;
if (text) {
  if (fixture) console.log('Deterministic model fixture; the file tool and sandbox are real.');
  target = (await request(`/sessions/${sid}/turns`, { submission_id: `turn-${key}`, text })).turn_id;
}
const input = createInterface({ input: process.stdin, output: process.stdout });
const answered = new Set();
async function interactions(snapshot) {
  for (const i of snapshot.interactions) {
    if (answered.has(i.id)) continue;
    const details = JSON.parse(i.call.arguments || '{}');
    const question = ['question', 'compaction'].includes(i.kind);
    if (!question && i.kind !== 'approval') throw Error('Unsupported interaction kind');
    const options = question ? (details.options || []) : ['once', 'session', 'deny'];
    console.log(question ? JSON.stringify(details.question) : `Approval: ${JSON.stringify(i.call)}`);
    let answer;
    if (!options.length) answer = await input.question('Answer: ');
    else {
      console.log(options.map((v, n) => `${n + 1}. ${v}`).join('\n'));
      let selection;
      do { selection = Number(await input.question('Choose a number: ')) - 1; } while (!Number.isInteger(selection) || selection < 0 || selection >= options.length);
      answer = options[selection];
    }
    const binding = Object.fromEntries(['bundle_digest', 'schema_digest', 'policy_digest', 'resource_policy_digest'].map(k => [k, i[k]]));
    await request(`/sessions/${sid}/controls`, { request_id: `answer-${i.id}`, turn_id: i.turn_id, action: { kind: 'response', interaction_id: i.id, response: answer, binding } });
    answered.add(i.id);
  }
}
const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted', 'truncated']);
let cursor = 0;
let done = false;
const initial = await request(`/sessions/${sid}`);
target ||= initial.turn?.id;
cursor = initial.cursor;
await interactions(initial);
if (initial.turn?.id === target && terminal.has(initial.turn.status)) done = true;
let failures = 0;
try {
  while (!done) {
    try {
      const response = await fetch(`${bases[node % bases.length]}/sessions/${sid}/events`, { headers: { ...headers, 'Last-Event-ID': String(cursor) } });
      if (!response.ok) throw Error(`Progress unavailable (${response.status})`);
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      try {
        while (!done) {
          const chunk = await reader.read(); if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true });
          let end;
          while ((end = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
            const data = frame.split('\n').find(line => line.startsWith('data: '));
            if (!data) continue;
            const event = JSON.parse(data.slice(6));
            if (!Number.isInteger(event.event_id)) throw Error('Progress stream needs reconciliation');
            if (event.event_id !== cursor + 1) throw Object.assign(Error('Cursor gap: load a fresh snapshot before continuing'), { permanent: true });
            cursor = event.event_id; failures = 0;
            console.log(`${cursor}: ${event.type}`);
            if (event.type === 'interaction_required') await interactions(await request(`/sessions/${sid}`));
            if (event.type === 'turn_completed' && event.turn_id === target) done = true;
          }
        }
      } finally { await reader.cancel(); }
      if (!done) throw Error('Progress connection closed');
    } catch (e) {
      if (e.permanent || ++failures > 5) throw e;
      node++; console.log(`Reconnecting from cursor ${cursor}...`);
      await new Promise(resolve => setTimeout(resolve, Math.min(failures * 250, 2000)));
    }
  }
  const final = await request(`/sessions/${sid}`);
  console.log(JSON.stringify({ status: final.turn?.status, messages: final.messages.slice(-2), cursor: final.cursor }, null, 2));
} finally { input.close(); }
