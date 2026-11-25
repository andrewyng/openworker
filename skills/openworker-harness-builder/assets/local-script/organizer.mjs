import { Harness, AttemptOutput } from '@openworkerai/harness-sdk';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline/promises';
import { stdin, stdout } from 'node:process';
import { fileTools } from './file-tools.mjs';

export async function createOrganizer(harness, directory, model, activity) {
  await harness.registerTools(await fileTools(directory, activity));
  const agent = harness.createAgent({
    // Persisted definitions are immutable. Change this name when changing the
    // prompt, tool set, approvals or other definition settings.
    name: 'file-organizer-v1',
    instructions: 'Organize only the demo files using files tools. Read before moving. Never overwrite files. Respect denials; report incomplete work.',
    defaultModel: model,
    tools: ['files.list_files', 'files.read_file', 'files.create_directory', 'files.delete_directory', 'files.move_file'],
    planning: false, memory: false, scheduling: false,
    builtinTools: { askUser: false, readHistory: false },
    approvals: { mode: 'only', tools: ['files.create_directory', 'files.delete_directory', 'files.move_file'] },
    limits: { maxModelCalls: 30, maxOutputTokens: 24000, maxOutputTokensPerCall: 2048, summaryTokens: 512 },
  });
  await agent.ready();
  return agent;
}

// ui.text replaces the whole answer; ui.event upserts activity, not raw HTML.
// ui.approve shows interruption.tool AND interruption.arguments, then returns
// true or false. A browser app implements it with an approval card.
export async function streamWithApprovals(session, text, ui) {
  const output = new AttemptOutput();
  let stream = (await session.send(text)).events();
  while (true) {
    for await (const item of stream) {
      output.apply(item);
      ui.event?.(item);
      ui.text?.(output.text);
    }
    const result = await stream.result();
    if (result.status !== 'interrupted') {
      if (result.status === 'completed') ui.text?.(result.finalOutput ?? output.text);
      else { output.interrupt(); ui.text?.(output.text); }
      return result;
    }
    if (!result.interruptions.length) throw Error('Interrupted without a pending interaction');
    for (const interruption of result.interruptions) {
      // Safety holds need the safety-specific recovery flow. Do not treat them
      // as approvals or automatically answer an unexpected question.
      if (interruption.kind !== 'approval') return result;
      const allowed = await ui.approve(interruption);
      await session.respond(interruption, { decision: allowed ? 'once' : 'deny' });
    }
    // Continue the SAME submission, replaying from the last consumed cursor.
    // Sending the original text again would create a different turn.
    stream = await session.resumeStreamed(stream.checkpoint());
  }
}

async function main() {
  const model = process.env.MODEL_ID;
  const anthropic = process.env.MODEL_API === 'anthropic';
  const provider = anthropic ? 'Anthropic' : 'OpenAI';
  const apiKeyEnv = anthropic ? 'ANTHROPIC_API_KEY' : 'OPENAI_API_KEY';
  if (!model || !process.env[apiKeyEnv]) throw Error(`Set MODEL_ID and ${apiKeyEnv} before starting`);
  // Fresh, dedicated workspace and separate durable state: nothing is seeded
  // into an existing user directory. Print both paths for later inspection.
  const demo = await mkdtemp(join(tmpdir(), 'openworker-organizer-'));
  const workspace = join(demo, 'files');
  const { mkdir } = await import('node:fs/promises');
  await mkdir(workspace);
  for (let i = 0; i < 10; i++) {
    const category = ['meeting', 'recipe', 'travel', 'project', 'receipt'][i % 5];
    await writeFile(join(workspace, `note-${i + 1}.txt`), `${category}: synthetic sample ${i + 1}\n`, { flag: 'wx' });
  }
  const stateDir = join(demo, 'state');
  console.log({ workspace, stateDir });
  const terminal = createInterface({ input: stdin, output: stdout });
  let harness, session;
  const cancel = () => { void session?.cancel().catch(() => {}); terminal.close(); };
  process.on('SIGINT', cancel);
  try {
    harness = await Harness.start({ stateDir, sandbox: 'off' });
    await harness.setModelProvider({ name: provider, api: anthropic ? 'anthropic' : 'responses',
      credentials: { apiKeyEnv, storage: 'memory' } });
    const agent = await createOrganizer(harness, workspace, { provider, name: model },
      item => console.log('Tool', JSON.stringify(item)));
    session = await agent.newSession({ workspace, scratch: false });
    console.log('Session reference:', JSON.stringify(session.reference()));
    let displayed = '';
    const result = await streamWithApprovals(session,
      'Inspect the ten files. Create appropriate category directories and move the files into them. Summarize what changed.', {
        approve: async pending => {
          console.log('Proposed operation:', pending.tool, JSON.stringify(pending.arguments));
          const answer = await terminal.question('Allow this operation once? [y/N] ');
          return answer.trim().toLowerCase() === 'y';
        },
        event: item => {
          const kind = item.summary?.type ?? item.event.type;
          if (['model_attempt_started', 'model_activity', 'tool_started', 'tool_finished'].includes(kind))
            console.log('Activity:', JSON.stringify(item.summary));
        },
        text: value => { if (value !== displayed) { displayed = value; console.log('Answer:', value); } },
      });
    console.log('Run status:', result.status);
    if (result.status !== 'completed') process.exitCode = 1;
  } finally {
    process.off('SIGINT', cancel);
    terminal.close();
    await harness?.shutdown();
    // Keep the demo workspace and state so the user can inspect the outcome.
  }
}
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url)
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
