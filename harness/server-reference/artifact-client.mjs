// Node 20+. Explicitly upload one caller-selected file, never scan a workspace.
import { open, readFile } from 'node:fs/promises';
import { basename } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
const args = process.argv.slice(2);
const option = name => { const i = args.indexOf(name); return i < 0 ? undefined : args[i + 1]; };
const component = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(value);
const sid = option('--session'), tid = option('--turn'), file = option('--file');
const key = option('--request-id') || randomUUID();
if (!component(sid) || !component(tid) || !file || !/^[A-Za-z0-9_-]{1,100}$/.test(key)) throw Error('Pass --session ID --turn ID --file PATH [--request-id ID]');
const base = new URL(process.env.OW_API_URL || 'http://127.0.0.1:8081');
if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname) || base.username || base.password || base.pathname !== '/' || base.search || base.hash) throw Error('Reference client expects a loopback API origin');
if (!process.env.OW_API_TOKEN_FILE) throw Error('Set OW_API_TOKEN_FILE');
const token = (await readFile(process.env.OW_API_TOKEN_FILE, 'utf8')).trim();
if (!/^[A-Za-z0-9_.-]{32,256}$/.test(token)) throw Error('Invalid token file');
async function request(path, method, body, headers = {}) {
  const response = await fetch(new URL(path, base), { method, headers: { Authorization: `Bearer ${token}`, ...headers }, body, duplex: 'half', redirect: 'error', signal: AbortSignal.timeout(125_000) });
  if (!response.ok) throw Error(`Artifact request failed (${response.status}); reconcile the printed ID, or retry the exact file/request against another API. No generating tool was repeated.`);
  return response.json();
}
console.log(`Request ID: ${key} (reuse with the identical file/session/turn after response loss)`);
const handle = await open(file, 'r');
try {
  const stat = await handle.stat();
  if (!stat.isFile() || stat.size > 64 * 1024 * 1024) throw Error('Requires a regular file of at most 64 MiB');
  const hash = createHash('sha256'); let size = 0;
  for await (const chunk of handle.createReadStream({ start: 0, autoClose: false })) {
    size += chunk.length;
    if (size > 64 * 1024 * 1024) throw Error('File grew beyond the size limit');
    hash.update(chunk);
  }
  if (size !== stat.size) throw Error('File changed while hashing');
  const path = `/sessions/${sid}/artifacts`;
  const artifact = await request(path, 'POST', JSON.stringify({ request_id: key, turn_id: tid, name: basename(file), content_type: 'application/octet-stream', size, sha256: hash.digest('hex') }), { 'Content-Type': 'application/json' });
  console.log(`Artifact: ${artifact.id}`);
  const ready = artifact.status === 'ready' ? artifact : await request(`${path}/${artifact.id}/content`, 'PUT', handle.createReadStream({ start: 0, autoClose: false }), { 'Content-Type': 'application/octet-stream', 'Content-Length': String(size) });
  console.log(JSON.stringify(ready, null, 2));
} finally { await handle.close(); }
