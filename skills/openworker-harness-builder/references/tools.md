# Custom tools and directory scope

Register application callbacks before creating the agent. The returned JSON is
the model's tool result; tools run in Node and may be asynchronous.

```typescript
await harness.registerTools({ namespace: 'files', tools: {
  read_file: {
    description: 'Read a UTF-8 file inside the selected directory.',
    inputSchema: { type: 'object', properties: { path: { type: 'string' } },
      required: ['path'], additionalProperties: false },
    execute: async (args, context) => {
      context.signal.throwIfAborted();
      await context.progress('Reading selected file');
      // Supply an app-owned confined reader, NOT readFile(args.path).
      return { text: await readConfinedFile(context.workspace, args.path) };
    },
  },
} });
const agent = harness.createAgent({
  name: 'directory-reader-v1', instructions: 'Summarize the selected files.',
  defaultModel: { provider: 'OpenAI', name: modelId },
  tools: ['files.read_file'], approvals: { mode: 'always' },
  planning: false, memory: false, scheduling: false,
  builtinTools: { askUser: false, readHistory: false },
});
await agent.ready();
const session = await agent.newSession({ workspace: absoluteDirectory, scratch: false });
```

`readConfinedFile` is application code. The complete executable
[file tools](../assets/local-script/file-tools.mjs) show directory listing, bounded
file reads, create/delete-empty-directory and non-overwriting moves. They restrict
paths to one canonical root, reject absolute/traversal paths and symlinks, verify
the session workspace, and expose no shell command. Use the
[Node script](local-script.md) to run them with approvals.

## Scope is not process isolation

With `sandbox: 'off'`, application callbacks retain application privileges.
`workspace` alone cannot confine arbitrary Node filesystem calls. Schema
validation and model instructions are not path enforcement. Every file handler
must enforce scope, including both source and destination of a move. Do not offer
a generic shell tool as a shortcut around the file handlers.

The bundled path checks assume an app-owned directory without other concurrent
writers. A separate process can race path checks by replacing directory entries;
these checks are not a kernel security boundary. For hostile code or concurrent
untrusted filesystem writers, use an isolated execution backend and run the
filesystem tools there. Node callbacks do not move into OpenShell merely because
the harness uses it for other tools. For backend setup, see the optional public
[OpenShell guide](https://openworker.com/docs/sdk/openshell/).

Move operations can have an uncertain/partial outcome after a crash or failed
unlink. Reconcile actual file state before retrying; approval is not a guarantee
of exactly-once filesystem execution.

## Tool names, arguments and activity

Register and select `files.move_file`. Its model-facing alias is
`files__move_file`; current events and approval requests may use that alias.
Keep a mapping from your registered names to aliases for display. Do not rename
tool identities in saved event payloads.

The callback receives arguments directly, plus `context.sessionId`, `turnId` and
`callId`. Use that tuple to upsert an activity item. Callback delivery can precede
your consumption of the journal's `tool_started`; do not rely on arrival order.

```typescript
for await (const item of run.events()) {
  if (item.event.type === 'tool_started') {
    const data = item.event.data as Record<string, unknown>;
    updateActivity({ sessionId: item.sessionId, turnId: item.event.turn_id,
      callId: data.call_id, tool: data.name, arguments: data.arguments });
  }
  if (item.summary?.type === 'tool_finished') {
    finishActivity(item.sessionId, item.event.turn_id, item.summary.callId);
  }
}
```

`updateActivity`/`finishActivity` are app UI functions. Typed start/finish summaries
currently omit arguments; retain them from the raw start event or callback.
Support absent/withheld data and render arguments as escaped text. Show only
product-appropriate fields; arguments can contain sensitive user content.
`context.progress(message, data)` supplies intermediate tool updates without new
polling. `model_activity` is an elapsed-wait signal, not proof of task progress.
