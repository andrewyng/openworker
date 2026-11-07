// Test-only credential transport for an isolated Linux host. Not shipped.
// Send {"api_key":"..."} over private stdin, never argv or an on-disk file.
let input='';
for await (const chunk of process.stdin) {
  input+=chunk;
  if (input.length>16384) throw new Error('Credential input exceeds limit');
}
let credential;
try { credential=JSON.parse(input); }
catch { throw new Error('Invalid credential input'); }
if (typeof credential.api_key!=='string'||!credential.api_key) {
  throw new Error('Missing test credential');
}
process.env.OPENAI_API_KEY=credential.api_key;
input=''; credential=null;
process.argv.push('--live');
await import('../examples/stdio-client.mjs');
