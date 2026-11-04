# Build apps with the OpenWorker skill

`openworker-apps/` contains one portable skill with desktop and web references.
It teaches a coding agent to use the maintained SDK examples, configure a model,
create sessions, stream results and prepare application packaging.

From this OpenWorker checkout, install it into an **existing app project**:

```sh
node skills/install.mjs --agent codex --project /path/to/your-app
```

For Claude Code, use `--agent claude`. The command uses Node's filesystem APIs,
so the same command works on Windows with a quoted Windows project path. It
creates `.agents/skills/openworker-apps/` for Codex or
`.claude/skills/openworker-apps/` for Claude Code. It never edits global settings,
installs dependencies or overwrites an existing skill. Review/back up an existing
installation before replacing it. If copying into a different app repo, use a
full path to this `install.mjs`.

Open a new coding-agent session in the destination project, then ask:

> Use the openworker-apps skill to build a desktop app that summarizes a selected code directory. Start from Code Atlas and explain the SDK calls as you build. Run the relevant tests and prepare a local package.

For the web track:

> Use the openworker-apps skill to build a web app where I can upload a PDF and ask questions with page references. Start from PDF Notes, explain the agent and session code, and prepare container deployment files.

You can also invoke `$openworker-apps` in Codex or `/openworker-apps` in Claude Code.
The installer records the local source checkout and its commit in
`references/checkout.json`; the skill checks that locator against the app's SDK
pin. It does not copy application source or grant permission to deploy.
That generated locator contains a local path; omit it when sharing the installed
skill with others, or reinstall from their own checkout. The skill works without
it by locating the matching repository source.

Manual installation is also possible: copy the entire `openworker-apps/` directory
into the appropriate project skills directory. Keep `SKILL.md`, `references/` and
`agents/` together. This guide targets local Codex and Claude Code sessions;
Claude web/Cowork skill installation is a different workflow.

Installation locations follow the official [Codex skill documentation](https://learn.chatgpt.com/docs/build-skills)
and [Claude Code skill documentation](https://code.claude.com/docs/en/skills).

Validate the installer in temporary projects with `node --test skills/install.test.mjs`.
