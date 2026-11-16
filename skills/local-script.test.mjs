import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, lstat, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createServer } from 'node:http';
import { install } from './install.mjs';
import { fileTools } from './openworker-harness-builder/assets/local-script/file-tools.mjs';

test('bundled file handlers confine paths, honor cancellation and preserve existing files', async t => {
  const root = await mkdtemp(join(tmpdir(), 'ow-skill-tools-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const workspace = join(root, 'files'); await mkdir(workspace);
  await writeFile(join(workspace, 'note.txt'), 'sample');
  await writeFile(join(root, 'outside.txt'), 'private');
  const calls = [], progress = [];
  const tools = (await fileTools(workspace, item => calls.push(item))).tools;
  const controller = new AbortController();
  const context = { workspace, sessionId: 's', turnId: 't', callId: 'c', signal: controller.signal,
    progress: async message => { progress.push(message); } };
  assert.equal((await tools.read_file.execute({ path: 'note.txt' }, context)).text, 'sample');
  for (const path of ['../outside.txt', '/etc/passwd', 'a/../../outside.txt', 'C:\\outside.txt'])
    await assert.rejects(() => tools.read_file.execute({ path }, context));
  if (process.platform !== 'win32') {
    await symlink(join(root, 'outside.txt'), join(workspace, 'linked.txt'));
    await assert.rejects(() => tools.read_file.execute({ path: 'linked.txt' }, context), /Symlinks/);
    await symlink(root, join(workspace, 'linked-dir'));
    await assert.rejects(() => tools.create_directory.execute({ path: 'linked-dir/new' }, context), /Symlinks/);
  }
  await assert.rejects(() => tools.read_file.execute({ path: 'note.txt' }, { ...context, workspace: root }), /Wrong workspace/);
  await tools.create_directory.execute({ path: 'notes' }, context);
  await writeFile(join(workspace, 'notes/existing.txt'), 'keep');
  await assert.rejects(() => tools.move_file.execute({ from: 'note.txt', to: 'notes/existing.txt' }, context));
  assert.equal(await readFile(join(workspace, 'notes/existing.txt'), 'utf8'), 'keep');
  assert.equal(await readFile(join(workspace, 'note.txt'), 'utf8'), 'sample');
  await tools.move_file.execute({ from: 'note.txt', to: 'notes/note.txt' }, context);
  await assert.rejects(() => lstat(join(workspace, 'note.txt')), { code: 'ENOENT' });
  assert.equal(await readFile(join(workspace, 'notes/note.txt'), 'utf8'), 'sample');
  await assert.rejects(() => tools.delete_directory.execute({ path: '.' }, context), /demo root/);
  await assert.rejects(() => tools.delete_directory.execute({ path: 'notes' }, context));
  await tools.create_directory.execute({ path: 'empty' }, context);
  await tools.delete_directory.execute({ path: 'empty' }, context);
  controller.abort();
  await assert.rejects(() => tools.create_directory.execute({ path: 'cancelled' }, context));
  assert.equal(calls[0].callId, 'c'); assert.ok(progress.length > 0);
  assert.equal(await readFile(join(root, 'outside.txt'), 'utf8'), 'private');
});

// Set this to a consumer's node_modules containing the published SDK/runtime.
// No source build, cloud credentials or scripted Rust host is needed.
test('installed organizer streams and resumes real Rust approvals without replaying tools', {
  skip: !process.env.OW_SKILL_SDK_NODE_MODULES, timeout: 60000,
}, async t => {
  const root = await mkdtemp(join(tmpdir(), 'ow-skill-native-'));
  const calls = [], events = [], approvals = [], requests = [], text = [];
  let harness;
  const plan = [
    ['list_files', { path: '.' }], ['read_file', { path: 'note.txt' }],
    ['create_directory', { path: 'notes' }],
    ['move_file', { from: 'note.txt', to: 'notes/note.txt' }],
    ['create_directory', { path: 'denied' }],
  ];
  const server = createServer(async (req, res) => {
    try {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const input = JSON.parse(raw); requests.push(input);
      const step = requests.length - 1;
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      const send = value => res.write(`data: ${JSON.stringify(value)}\n\n`);
      if (step < plan.length) {
        const [name, args] = plan[step];
        send({ id: `fixture-${step}`, choices: [{ index: 0, delta: { role: 'assistant',
          tool_calls: [{ index: 0, id: `call-${step}`, type: 'function',
            function: { name: `files__${name}`, arguments: JSON.stringify(args) } }] }, finish_reason: null }] });
        send({ id: `fixture-${step}`, choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] });
      } else {
        send({ id: 'final', choices: [{ index: 0, delta: { role: 'assistant', content: 'Organized ' }, finish_reason: null }] });
        send({ id: 'final', choices: [{ index: 0, delta: { content: 'one file; respected the denial.' }, finish_reason: 'stop' }] });
      }
      send({ id: step < plan.length ? `fixture-${step}` : 'final', choices: [], usage: { prompt_tokens: 80, completion_tokens: 10, total_tokens: 90 } });
      res.end('data: [DONE]\n\n');
    } catch { res.writeHead(500); res.end(); }
  });
  await new Promise(done => server.listen(0, '127.0.0.1', done));
  t.after(async () => {
    await harness?.shutdown(); server.closeAllConnections();
    await new Promise(done => server.close(done));
    await rm(root, { recursive: true, force: true });
  });
  await symlink(resolve(process.env.OW_SKILL_SDK_NODE_MODULES), join(root, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  const installed = await install({ agent: 'codex', project: root });
  const { createOrganizer, streamWithApprovals } = await import(pathToFileURL(join(installed, 'assets/local-script/organizer.mjs')));
  const { Harness } = await import(pathToFileURL(join(root, 'node_modules/@openworkerai/harness-sdk/dist/index.js')));
  const workspace = join(root, 'workspace'); await mkdir(workspace);
  await writeFile(join(workspace, 'note.txt'), 'project: sample');
  harness = await Harness.start({ stateDir: join(root, 'state'), sandbox: 'off' });
  await harness.setModelProvider({ name: 'Fixture', api: 'chat-completions',
    baseUrl: `http://127.0.0.1:${server.address().port}/v1` });
  const agent = await createOrganizer(harness, workspace, { provider: 'Fixture', name: 'fixture' }, item => calls.push(item));
  const session = await agent.newSession({ workspace, scratch: false });
  const result = await streamWithApprovals(session, 'Organize the sample.', {
    event: item => events.push(item), text: value => text.push(value),
    approve: async item => {
      approvals.push(item);
      assert.ok(item.arguments, 'Show actual proposed arguments');
      if (item.arguments.path === 'notes') await assert.rejects(() => lstat(join(workspace, 'notes')), { code: 'ENOENT' });
      if (item.arguments.to) assert.equal(await readFile(join(workspace, 'note.txt'), 'utf8'), 'project: sample');
      return item.arguments.path !== 'denied';
    },
  });
  assert.equal(result.status, 'completed', JSON.stringify({ result, requests: requests.length,
    failures: events.filter(e => /error|failed/.test(e.event.type)).map(e => e.event) }));
  assert.equal(result.finalOutput, 'Organized one file; respected the denial.');
  assert.equal(text.at(-1), result.finalOutput);
  assert.equal(approvals.length, 3, 'Reads do not prompt; mutations prompt once each');
  assert.equal(calls.length, 4, 'Denied creation never executes and previous callbacks do not replay');
  assert.equal(requests.length, 6);
  assert.equal(requests[0].tools.length, 5, 'Only selected file tools reach the model');
  assert.ok(events.some(e => e.summary?.type === 'assistant_delta'));
  assert.ok(events.some(e => e.event.type === 'tool_started' && e.event.data.arguments));
  const ids = events.map(e => `${e.sessionId}/${e.event.event_id}`);
  assert.equal(new Set(ids).size, ids.length, 'Checkpoint continuation must avoid replaying consumed events');
  assert.equal(await readFile(join(workspace, 'notes/note.txt'), 'utf8'), 'project: sample');
  await assert.rejects(() => lstat(join(workspace, 'denied')), { code: 'ENOENT' });
  const lastMessages = requests.at(-1).messages;
  assert.ok(lastMessages.some(m => m.role === 'tool' && m.content.includes('denied')), 'Working model sees denial');
});
