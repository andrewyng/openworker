# Build apps with the OpenWorker skill

One `openworker-apps` skill helps Codex and Claude Code build Desktop and Web
applications with the OpenWorker Harness SDK. It includes version-matched SDK
snippets and separate Desktop/Web references. No OpenWorker checkout is required
for the packaged skill, and installing it does not install the SDK or a runtime.

**Release candidate:** `@openworkerai/build-with-sdk-skills@0.1.0-beta.1` is prepared
locally. Registry commands below apply after publication; the version in this
repository does not mean it has been published.

## Install in your app project

After publication, from an existing app project:

```sh
npx @openworkerai/build-with-sdk-skills@0.1.0-beta.1 --agent codex --project .
```

For Claude Code:

```sh
npx @openworkerai/build-with-sdk-skills@0.1.0-beta.1 --agent claude --project .
```

The installer creates `.agents/skills/openworker-apps/` for Codex or
`.claude/skills/openworker-apps/` for Claude Code. It supports quoted project paths
on Windows and macOS, leaves global settings alone, and refuses to overwrite an
existing skill. Review/back up an existing installation before replacing it.
`references/installation.json` records the matching SDK version, without local
checkout paths or credentials. Use the same version for the SDK and skill.

The skill is discoverable in that project. Restart the coding-agent session if it
does not appear. Invoke `$openworker-apps` in Codex or `/openworker-apps` in Claude
Code, or ask:

> Use the openworker-apps skill to build a desktop app that summarizes a selected code directory. Explain the SDK calls as you build, test the app, and prepare an unpacked desktop package.

For the Web track:

> Use the openworker-apps skill to build a web app where I can upload a PDF and ask questions with page references. Explain the agent and session code and test the application locally.

The shared skill contains the app integration guidance. Detailed Railway/Vercel
recipes and a cluster-operations skill will follow separately. This installer
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
npm exec --package /absolute/new-skills-candidate/archives/openworkerai-build-with-sdk-skills-0.1.0-beta.1.tgz -- build-with-sdk-skills --agent codex --project /path/to/your-app
```

The preparer never publishes. Source package manifests remain private; staged
candidates remove that marker for the separately reviewed publication step.

Installation locations follow the official [Codex skill documentation](https://learn.chatgpt.com/docs/build-skills)
and [Claude Code skill documentation](https://code.claude.com/docs/en/skills).

Validate source and packed installation with `node --test skills/install.test.mjs skills/package.test.mjs`.
