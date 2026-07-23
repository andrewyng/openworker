# OpenWorker

**[openworker.com](https://openworker.com)** · [Download](#download) · [Issues](https://github.com/andrewyng/openworker/issues)

> **Beta** - OpenWorker is in open beta: fully usable, updates itself, and we're actively polishing rough edges. [Issues](https://github.com/andrewyng/openworker/issues) welcome.

OpenWorker is an open-source AI coworker that delivers **finished work**, not just chat: a polished document, a Slack reply with the numbers, an updated calendar, a triaged inbox.

Ask it to prepare a customer brief, untangle your week, draft a report, or check where a release stands across Jira and GitHub. It works across your files and everyday tools, produces the deliverable, and **checks in before doing anything consequential**.

It runs on your machine and doesn't lock you into any model: bring your own API key for OpenAI, Anthropic, Google, or an open-weight provider, or run fully local with Ollama. Your data leaves your machine only through the model and integrations *you* choose.

## Download

[**⬇ macOS (Apple Silicon)**](https://github.com/andrewyng/openworker/releases/latest/download/OpenWorker-macos-arm64.dmg)
<sub>macOS 12+ · signed & notarized · auto-updates</sub>

[**⬇ Windows 10/11 (x64)**](https://github.com/andrewyng/openworker/releases/latest/download/OpenWorker-windows-setup.exe)
<sub>builds are not yet code-signed, so SmartScreen will warn; signing is in progress</sub>

Open the app, add a model key (or point it at Ollama), and ask for something real.

## What it can do

- **Produce real deliverables** - documents, spreadsheets, reports, and web pages land as files you can open and share, not text in a chat window.
- **Work from Slack** - mention `@OpenWorker` in a channel; a session opens on your desktop, the work happens with your tools, and the answer comes back as a thread reply.
- **Use your everyday tools** - 25+ integrations including GitHub, Slack, Jira, Notion, Linear, HubSpot, Outlook, monday.com, Gmail, and Google Calendar, plus your **terminal and local files**. Remote MCP servers plug in too, with per-tool control.
- **Run on a schedule** - automations for recurring work: a morning brief, a weekly report, a standing watch over a channel. Runs land in the app with full transcripts.
- **Ask before acting** - writes, sends, and shell commands are approval-gated. Unattended runs park their asks in an inbox instead of acting on their own.

## Bring your own model

Model access is yours: pick a provider, paste your key, switch anytime. Supported out of the box:

**OpenAI · Anthropic · Google Gemini · GLM (Z.ai) · DeepSeek · Kimi (Moonshot) · Qwen · MiniMax · Mistral · Grok (xAI)** - plus open-weight models via **Together** and **Fireworks**, and fully local models via **Ollama**.

A curated model list marks what we've verified for tool-calling work; any model string works at your own risk. Keys are stored locally and are only ever sent to the vendor they belong to.

## How it works

```text
┌────────────────────────────────────────────────┐
│              OpenWorker desktop app            │  native shell + GUI
├────────────────────────────────────────────────┤
│           local agent server (Python)          │  engine · tools · connectors - built on aisuite
├───────────────┬────────────────┬───────────────┤
│  your files   │   your tools   │  your model   │  everything runs with your keys,
│  & terminal   │ 25+ connectors │  any provider │  on your machine
└───────────────┴────────────────┴───────────────┘
```

Everything lives on your machine: the agent loop, your conversations, connector tokens, and model keys. A small cloud service exists only to make connector OAuth one-click; the tokens it brokers are handed straight back to your device and stored locally. No account is required: every connector also accepts manually-created credentials, and the app is fully usable signed out.

## Run from source

```shell
git clone https://github.com/andrewyng/openworker
cd openworker
bash packaging/setup_dev_env.sh          # creates .venv and installs the package

# terminal 1 - the local agent server
.venv/bin/openworker-server --cwd ~/some/project --port 8765

# terminal 2 - the GUI (browser dev mode)
cd surfaces/gui && npm install && npm run dev
```

Tests: `.venv/bin/pytest` (server), `npm test` and `npm run e2e` in `surfaces/gui` (GUI unit + hermetic end-to-end). Desktop bundles are built with `packaging/build_dmg.sh` / `packaging/build_windows.ps1`. Design notes and specs live in [`docs/`](docs/).

## Built on aisuite

OpenWorker's engine is built on [**aisuite**](https://github.com/andrewyng/aisuite), a lightweight Python library providing a unified chat-completions API across LLM providers and an agents layer with tools, toolkits, and MCP support. If you want to build your own agent harness rather than use ours, start there; this repo is a working reference for what aisuite can carry.

OpenWorker was originally developed inside the aisuite repository before moving to its own home here; thanks to the aisuite contributors whose work it builds on.

## License

MIT - see [LICENSE](LICENSE). Contributions and bug reports are welcome; the app updates itself, so fixes reach installs quickly.
