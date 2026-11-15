// Run after npm run build. Set OPENWORKER_EXECUTABLE and OPENWORKER_MODEL.
// Use OPENAI_API_KEY privately, or OPENWORKER_LOCAL_URL for a no-key local server.
import { Harness } from '../dist/index.js';
const model = process.env.OPENWORKER_MODEL;
if (!model) throw Error('Set OPENWORKER_MODEL to an exact model ID');
const local = process.env.OPENWORKER_LOCAL_URL;
const provider = local ? 'Local' : 'OpenAI';
const harness = await Harness.start({ appId: 'desktop-sdk-example', sandbox: 'off' });
try {
  await harness.setModelProvider(local
    ? { name: provider, api: 'chat-completions', baseUrl: local }
    : { name: provider, api: 'responses', credentials: { apiKeyEnv: 'OPENAI_API_KEY', storage: 'memory' } });
  await harness.registerTools({ namespace: 'app', tools: { status: {
    description: 'Display a short status in this application.',
    inputSchema: { type: 'object', properties: { text: { type: 'string', maxLength: 1000 } }, required: ['text'], additionalProperties: false },
    execute: async ({text}, context) => {
      context.signal.throwIfAborted();
      console.log('App status:', text); // Forward to your own desktop UI.
      return { displayed: true };
    },
  } } });
  const agent = harness.createAgent({ name: 'desktop-example', instructions: 'Help the user. Use app.status for a short status update.', defaultModel: { provider, name: model }, tools: ['app.status'] });
  const session = await agent.newSession();
  console.log('Save this session reference:', session.reference());
  const run = await session.send('Suggest a tiny game I could build.');
  for await (const event of run.events()) { /* render event */ }
  const result = await run.result();
  console.log('Run status:', result.status);
  if (result.interruptions.length) console.log('Awaiting input. Render the durable interaction in your UI; never invent a response.');
} finally { await harness.shutdown(); }
