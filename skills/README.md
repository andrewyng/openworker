# Build apps with the OpenWorker skill

One `openworker-apps` skill helps Codex and Claude Code build Desktop and Web
applications with the OpenWorker Harness SDK. It includes version-matched SDK
snippets and separate Desktop/Web references. No OpenWorker checkout is required
for the packaged skill, and installing it does not install the SDK or a runtime.

This skill release targets SDK `0.1.0-beta.2`, including provider caching,
attempt-aware streaming and per-turn session options.

## Install the skill

From an existing app project, install the latest published skill:

```sh
npx @openworkerai/build-with-sdk-skills --agent codex --project .
```

For Claude Code:

```sh
npx @openworkerai/build-with-sdk-skills --agent claude --project .
```

These project commands create `.agents/skills/openworker-apps/` for Codex or
`.claude/skills/openworker-apps/` for Claude Code. It supports quoted project paths
on Windows and macOS, leaves global settings alone, and refuses to overwrite an
existing skill. Review/back up an existing installation before replacing it.
`references/installation.json` records the matching SDK version, without local
checkout paths or credentials. Use `sdkVersion` from that file for the SDK. Skill and SDK package versions may
differ; this skill release targets SDK `0.1.0-beta.2`.

### Across your projects

For user-wide installation, use `--global` instead of `--project`:

```sh
npx @openworkerai/build-with-sdk-skills --agent claude --global
```

```sh
npx @openworkerai/build-with-sdk-skills --agent codex --global
```

This installs into `~/.claude/skills/openworker-apps/` for Claude Code or
`~/.agents/skills/openworker-apps/` for Codex, making the skill available across
projects for your user on this machine. It does not install into Claude web or
Cowork, change account settings, or require `npm install -g` or administrator
access. Choose either `--global` or `--project`, not both. Both modes preserve an
existing installation.

Unversioned commands select npm's `latest` release. To pin an installation for
reproducibility, append `@<version>` to the package name. The installer records
the compatible SDK version separately; the skill and SDK versions may differ.

The skill is discoverable in the selected scope. Restart the coding-agent session if it
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
follow separately. This installer targets local Codex and Claude Code sessions.

## Claude workspace uploads

Claude's **Customize → Skills → Upload** is a separate installation route.
For that flow, zip the complete installed `openworker-apps` directory, including
its references, Docker assets and `references/installation.json`; uploading only
`SKILL.md` loses the supporting files. The npm `.tgz` package is not this upload
format. Uploading a skill provides instructions, not an installed SDK, runtime,
or permission to deploy an app. The workspace upload flow has not been qualified
by this package's local installer tests.

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
npm exec --package /absolute/new-skills-candidate/archives/openworkerai-build-with-sdk-skills-0.1.0-beta.3.tgz -- build-with-sdk-skills --agent codex --project /path/to/your-app
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
