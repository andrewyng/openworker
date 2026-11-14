# Build apps with the OpenWorker skill

One `openworker-harness-builder` skill helps Codex and Claude Code build Desktop, Web and Node.js
applications with the OpenWorker Harness SDK. It includes version-matched SDK
snippets, a scoped file-organizer example, a complete streaming approval loop and separate track references. No OpenWorker checkout is required
for the packaged skill, and installing it does not install the SDK or a runtime.

This skill release targets SDK `0.1.0-beta.3`, including provider caching,
attempt-aware streaming, per-turn session options, and auto-approver/compaction guidance.

## Install the skill

From an existing app project, install the latest published skill:

```sh
npx @openworkerai/openworker-harness-builder --agent codex --project .
```

For Claude Code:

```sh
npx @openworkerai/openworker-harness-builder --agent claude --project .
```

These project commands create `.agents/skills/openworker-harness-builder/` for Codex or
`.claude/skills/openworker-harness-builder/` for Claude Code. It supports quoted project paths
on Windows and macOS and leaves global settings alone. If a skill already exists,
an interactive terminal asks before replacing it and saves the old copy as a backup.
`references/installation.json` records the matching SDK version, without local
checkout paths or credentials. Use `sdkVersion` from that file for the SDK. Skill and SDK package versions may
differ; this skill release targets SDK `0.1.0-beta.3`.

### Across your projects

For user-wide installation, use `--global` instead of `--project`:

```sh
npx @openworkerai/openworker-harness-builder --agent claude --global
```

```sh
npx @openworkerai/openworker-harness-builder --agent codex --global
```

This installs into `~/.claude/skills/openworker-harness-builder/` for Claude Code or
`~/.agents/skills/openworker-harness-builder/` for Codex, making the skill available across
projects for your user on this machine. It does not install into Claude web or
Cowork, change account settings, or require `npm install -g` or administrator
access. Choose either `--global` or `--project`, not both. Both modes require
confirmation or the explicit replacement flag before replacing an existing skill.

### Update an existing installation

Run the same install command again. In an interactive terminal it asks:

```text
Replace the existing skill? A backup will be saved. [y/N]
```

Enter `y` or `yes` to replace it. Enter or `n` leaves it unchanged. For scripts,
or to skip the prompt explicitly, use:

```sh
npx @openworkerai/openworker-harness-builder --agent claude --global --replace-existing
```

```sh
npx @openworkerai/openworker-harness-builder --agent codex --project . --replace-existing
```

Non-interactive installs never prompt and require `--replace-existing` to update.
Every replacement saves the complete old directory, including custom files, under
`.claude/skill-backups/` or `.agents/skill-backups/` in the selected project or home.
The installer prints the exact backup path. Backups are outside the active skills
directory, so coding agents do not load duplicates. A new complete copy replaces
the old one; obsolete files are not merged into it. If activation fails, the
installer attempts to restore the previous copy and reports its backup location
if automatic restoration fails. Review customizations before copying any back.

An interrupted installer can leave an installation lock in `.claude/` or `.agents/`.
If the next install reports that lock, first confirm no installer is running,
then review and remove the reported lock directory before retrying. Existing
backups are never automatically deleted.

Skill and SDK publications are independent: updating this Skill does not replace
the README inside an already-installed SDK package. Use the bundled version-matched
references for these examples and `installation.json` for the exact SDK pin.

Unversioned commands select npm's `latest` release. To pin an installation for
reproducibility, append `@<version>` to the package name. The installer records
the compatible SDK version separately; the skill and SDK versions may differ.

The skill is discoverable in the selected scope. Restart the coding-agent session if it
does not appear. Invoke `$openworker-harness-builder` in Codex or `/openworker-harness-builder` in Claude
Code, or ask:

> Use the openworker-harness-builder skill to build a desktop app that summarizes a selected code directory. Explain the SDK calls as you build, test the app, and prepare an unpacked desktop package.

For the Web track:

> Use the openworker-harness-builder skill to build a web app where I can upload a PDF and ask questions with page references. Explain the agent and session code and test the application locally.

The shared skill includes a [Railway deployment track](openworker-harness-builder/references/railway.md)
and Docker assets. The default is one Node service serving the frontend and API,
with the Rust harness running as a managed child process. The recipe has been tested locally and on Railway with the
published SDK, including streamed Claude responses and conversation recovery
after redeployment. Vercel recipes and a cluster-operations skill will
follow separately. This installer targets local Codex and Claude Code sessions.

## Claude workspace uploads

Claude's **Customize → Skills → Upload** is a separate installation route.
For that flow, zip the complete installed `openworker-harness-builder` directory, including
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
npm exec --package /absolute/new-skills-candidate/archives/openworkerai-openworker-harness-builder-0.1.0-beta.6.tgz -- openworker-harness-builder --agent codex --project /path/to/your-app
```

The preparer never publishes. Source package manifests remain private; staged
candidates remove that marker for the separately reviewed publication step.
The Skill's exact `openworkerSdkVersion` pin is independent of the native-release
manifest in a checkout; preserve the SDK version qualified for that Skill release.

Installation locations follow the official [Codex skill documentation](https://learn.chatgpt.com/docs/build-skills)
and [Claude Code skill documentation](https://code.claude.com/docs/en/skills).

Validate source, file scope and packed installation with
`node --test skills/install.test.mjs skills/package.test.mjs skills/local-script.test.mjs`.
To include the native approval/resume test, set `OW_SKILL_SDK_NODE_MODULES` to an
absolute consumer `node_modules` containing the matching published SDK/runtime.
That test uses a loopback model fixture and does not spend model tokens.

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

When upgrading from the earlier `openworker-apps` skill, review and remove that old skill from the same installation scope after installing the new name. Replacement applies only to `openworker-harness-builder`; the old-named skill is left untouched.
