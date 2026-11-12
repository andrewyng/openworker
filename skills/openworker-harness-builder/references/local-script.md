# A plain Node.js file-organizing agent

Use this track for a terminal demo, local utility or automation without Electron,
a browser UI or a web server. Node starts the managed Rust subprocess; npm
installs the matching runtime. The agent uses real registered file tools and
requires human approval for every create-directory, delete-empty-directory and
move operation. Reads and directory listings do not ask for approval.

Read `installation.json` beside this file and install its `sdkVersion`:

```sh
npm init -y
npm install --save-exact @openworkerai/harness-sdk@0.1.0-beta.3
```

Copy both bundled files into your app, next to each other:

- [organizer.mjs](../assets/local-script/organizer.mjs): agent creation, streaming,
  approval prompts and session continuation.
- [file-tools.mjs](../assets/local-script/file-tools.mjs): scoped filesystem callbacks.

Then run it in a terminal with Node 20 or later. Choose an exact model ID your
account supports:

```sh
MODEL_ID=YOUR_OPENAI_MODEL node --env-file="$HOME/.env" organizer.mjs
```

For Anthropic:

```sh
MODEL_API=anthropic MODEL_ID=YOUR_CLAUDE_MODEL node --env-file="$HOME/.env" organizer.mjs
```

`~/.env` should contain `OPENAI_API_KEY=...` or `ANTHROPIC_API_KEY=...`.
Node's `--env-file` loads it; the SDK does not load the file by itself. On
PowerShell, set `$env:MODEL_ID='...'` (and `$env:MODEL_API='anthropic'` if needed),
then run `node --env-file="$HOME/.env" organizer.mjs`. In every case the provider
key is resolved in Node, so `inheritEnv` is unnecessary for it.

The script creates ten synthetic text files in a **new dedicated demo
directory**, prints that directory and its separate state path, and leaves both
for inspection. It does not organize an existing personal directory or delete
files. Answer `y` to approve an operation once; any other answer denies it. Ctrl-C
requests cancellation and shuts down the harness. Tool activity includes the
actual arguments and call identity, plus elapsed model-wait updates.

The sample uses `sandbox: 'off'` and deliberately scoped callbacks. Read
[Tools](tools.md) for the filesystem assumptions and [Approvals](approvals.md)
for adapting the terminal prompt to an approval card in a desktop/web app.
Keep file contents and model output as escaped text in a graphical UI.

The executable sample uses modern `createAgent`/`setModelProvider` APIs. Change
its versioned agent name if adapting the definition inside existing durable
state. For an existing session, register the identical definition and tools,
resupply memory-only credentials, then use `agent.resumeSession(reference)`.
