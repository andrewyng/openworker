# Build apps with the OpenWorker skill

One `openworker-apps` skill helps Codex and Claude Code build Desktop and Web
applications with the OpenWorker Harness SDK. It includes version-matched SDK
snippets and separate Desktop/Web references. No OpenWorker checkout is required
for the packaged skill, and installing it does not install the SDK or a runtime.

**Release candidate:** `@openworkerai/build-with-sdk-skills@0.1.0-beta.2` is prepared
locally. Registry commands below apply after publication; the version in this
repository does not mean it has been published.

## Install in your app project

After publication, from an existing app project:

```sh
npx @openworkerai/build-with-sdk-skills@0.1.0-beta.2 --agent codex --project .
```

For Claude Code:

```sh
npx @openworkerai/build-with-sdk-skills@0.1.0-beta.2 --agent claude --project .
```

The installer creates `.agents/skills/openworker-apps/` for Codex or
`.claude/skills/openworker-apps/` for Claude Code. It supports quoted project paths
on Windows and macOS, leaves global settings alone, and refuses to overwrite an
existing skill. Review/back up an existing installation before replacing it.
`references/installation.json` records the matching SDK version, without local
checkout paths or credentials. Use `sdkVersion` from that file for the SDK. Skill and SDK package versions may
differ; this skill candidate targets SDK `0.1.0-beta.1`.

The skill is discoverable in that project. Restart the coding-agent session if it
does not appear. Invoke `$openworker-apps` in Codex or `/openworker-apps` in Claude
Code, or ask:

> Use the openworker-apps skill to build a desktop app that summarizes a selected code directory. Explain the SDK calls as you build, test the app, and prepare an unpacked desktop package.

For the Web track:

> Use the openworker-apps skill to build a web app where I can upload a PDF and ask questions with page references. Explain the agent and session code and test the application locally.

The shared skill includes a [Railway deployment track](openworker-apps/references/railway.md)
and Docker assets. The default is one Node service serving the frontend and API,
with the Rust harness running as a managed child process. The recipe has been tested locally and on Railway with the
published SDK, including streamed Claude responses and conversation recovery
after redeployment. Vercel recipes and a cluster-operations skill will
follow separately. This installer
targets local Codex and Claude Code projects, not Claude web/Cowork uploads.

## Try the local candidate

From an OpenWorker checkout, the existing source command remains available:

```sh
node skills/install.mjs --agent codex --project /path/to/your-app
```

Prepare the standalone package without compiling or downloading a native runtime:

```sh
node skills/prepare.mjs /absolute/new-skills-candidate
```

Install the resulting archive into a temporary project or use it through npm exec:

```sh
npm exec --package /absolute/new-skills-candidate/archives/openworkerai-build-with-sdk-skills-0.1.0-beta.2.tgz -- build-with-sdk-skills --agent codex --project /path/to/your-app
```

The preparer never publishes. Source package manifests remain private; staged
candidates remove that marker for the separately reviewed publication step.

Installation locations follow the official [Codex skill documentation](https://learn.chatgpt.com/docs/build-skills)
and [Claude Code skill documentation](https://code.claude.com/docs/en/skills).

Validate source and packed installation with `node --test skills/install.test.mjs skills/package.test.mjs`.

## Railway recipe checks

The Railway reference and Docker assets are installed with the skill. To exercise
the container contract locally using a verified SDK artifact manifest:

```sh
OW_RAILWAY_CONTAINER_TEST=1 \
OW_SDK_PUBLICATION_MANIFEST=/absolute/path/to/publication-manifest.json \
node --test skills/railway-container.test.mjs
```

This opt-in test builds a Linux Node image from the bundled Dockerfile, supplies
the SDK/runtime archives without a source checkout dependency, and uses a local
model fixture. It checks native discovery, the configured port, readiness,
unauthorized requests, streamed text, clean shutdown and conversation recovery
after replacing the container with the same named volume. It also checks that
local secrets and host node_modules are excluded from the image. It cleans up
its own containers, volume, network and image. Debian packages may be downloaded
during image build; model traffic stays on an isolated Docker network.

This does not qualify live Railway routing, credentials, billing or deployment
replacement. Those checks require an authorized Railway target.

A separate live Railway check used SDK `0.1.0-beta.1`, the bundled Dockerfile,
one replica and a persistent `/data` volume. It verified HTTPS routing, rejected
unauthenticated requests, streamed output before model completion, explicit
cancellation, graceful shutdown, unchanged session references after redeployment,
and real Anthropic recall of earlier conversation content. This qualifies the
single-service recipe; it does not establish multi-replica or zero-downtime behavior.
