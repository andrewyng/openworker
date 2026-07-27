OpenWorker

openworker.com · Download · Issues

Beta - OpenWorker is in open beta: fully usable, updates itself, and we're actively polishing rough edges. Issues welcome.

AI that gets your everyday tasks done. OpenWorker is an open-source AI coworker that lives on your desktop and delivers finished work, not just chat: a polished document, a Slack reply with the numbers, an updated calendar, a triaged inbox.

It runs on your machine and doesn't lock you into any model: bring your own API key for OpenAI, Anthropic, Google, or an open-weight provider, or run fully local with Ollama. Your data leaves your machine only through the model and integrations you choose.

Show Image

Contents
Download
How it works
What it can do
Bring your own model
Privacy
Run from source
Repository layout
Built on aisuite
Contributing
Download

⬇ macOS (Apple Silicon) <sub>macOS 12+ · signed & notarized · auto-updates</sub>

⬇ Windows 10/11 (x64) <sub>builds are not yet code-signed, so SmartScreen will warn; signing is in progress</sub>

Open the app, add a model key (or point it at Ollama), and ask for something real.

How it works
Tell OpenWorker the outcome you want - "prepare a customer brief," "untangle my calendar," "draft a report," "check where the release stands across Jira and GitHub."
It breaks the task into steps and works across your desktop, files, and connected apps.
Before anything consequential - sending a message, changing a calendar, running a command - it checks in and you approve or redirect.
You get the finished deliverable, not a to-do list.

Under the hood:

text
┌────────────────────────────────────────────────┐
│              OpenWorker desktop app            │  native shell + GUI
├────────────────────────────────────────────────┤
│           local agent server (Python)          │  engine · tools · connectors - built on aisuite
├───────────────┬────────────────┬───────────────┤
│  your files   │   your tools   │  your model   │  everything runs with your keys,
│  & terminal   │ 25+ connectors │  any provider │  on your machine
└───────────────┴────────────────┴───────────────┘
What it can do
Produce real deliverables - documents, spreadsheets, reports, and web pages land as files you can open and share.
Work from Slack - mention @OpenWorker in a channel; a session opens on your desktop, the work happens with your tools, and the answer comes back as a thread reply.
Use your everyday tools - 25+ integrations including GitHub, Slack, Jira, Notion, Linear, HubSpot, Outlook, monday.com, Gmail, and Google Calendar, plus your terminal and local files. Any tool reachable over MCP plugs in too, with per-tool control.
Run on a schedule - automations for recurring work: a morning brief, a weekly report, a standing watch over a channel. Runs land in the app with full transcripts.
Ask before acting - writes, sends, and shell commands are approval-gated. Unattended runs park their asks in an inbox instead of acting on their own.
Bring your own model

Model access is yours: pick a provider, paste your key, switch anytime. Supported out of the box:

OpenAI · Anthropic · Google Gemini · Inkling (Thinking Machines) · GLM (Z.ai) · DeepSeek · Kimi (Moonshot) · Qwen · MiniMax · Mistral · Grok (xAI) - plus open-weight models via Together and Fireworks, and fully local models via Ollama.

A curated model list marks what we've verified for tool-calling work. Adding any model string works at your own risk.

Privacy

OpenWorker is local-first. Everything lives on your machine: the agent loop, your conversations, connector tokens, and model keys - all in the app's local secret store. The only cloud piece is a small service that brokers OAuth handshakes for connectors. You can always use the app without signing in - use the connectors via manually-created credentials/API keys.

Run from source

Prerequisites: Python 3.10+, Node 20+, and (for the desktop shell) the Rust toolchain via rustup.

shell
git clone https://github.com/andrewyng/openworker
cd openworker

# 1. One-time bootstrap - creates the Python venv at .venv
#    (on Windows, run from Git Bash or WSL)
bash packaging/setup_dev_env.sh

# 2. Start the local agent server
.venv/bin/openworker-server --cwd ~/some/project --port 8765
#    (Windows: .venv\Scripts\openworker-server.exe)

# 3. In a second terminal, start the UI
cd surfaces/gui
npm install
npm run dev        # browser UI on the Vite dev port

The standalone server creates a per-launch token at <state-dir>/sidecar-8765.token; Vite reads that user-only file when it starts. For direct API calls, send its value in the X-OpenWorker-Token header. The desktop app uses an in-memory launch token instead and never writes it to disk.

To run the full desktop app instead of the browser UI, replace step 3 with npm run tauri dev (from surfaces/gui/) - the Tauri shell launches the window and supervises the server itself.

Tests:

.venv/bin/pytest - backend server tests
npm test - GUI unit tests (from surfaces/gui/)
npm run e2e - hermetic end-to-end tests (from surfaces/gui/)

Desktop bundles: built with packaging/build_dmg.sh (macOS) or packaging/build_windows.ps1 (Windows).

Repository layout
Directory	What's in it
coworker/	Python backend - agent engine, model providers, connectors, MCP client, memory, automations
surfaces/gui/	Desktop app - React UI + Tauri shell that supervises the server
stt/	Speech-to-text sidecar (Rust) for voice input
packaging/	Installer builds (macOS DMG, Windows), auto-update manifest, dev bootstrap
docs/	Design specs and decision logs
tests/	Backend test suite
Built on aisuite

OpenWorker's engine is built on aisuite, a lightweight Python library providing a unified chat-completions API across LLM providers and an agents layer with tools, toolkits, and MCP support. If you want to build your own agent harness rather than use ours, start there; this repo is a working reference for what aisuite can carry.

OpenWorker was originally developed inside the aisuite repository before moving to its own home here; thanks to the aisuite contributors whose work it builds on.

Contributing

OpenWorker is in open beta, and we're actively working through rough edges - contributions and bug reports are welcome.

Found a bug or have a feature request? Open an issue - please include your OS, whether you're running the packaged app or from source, and steps to reproduce if it's a bug.
Want to contribute code? Fork the repo, create a branch off main, and open a pull request. Small, focused PRs (one fix or feature per PR) are easiest to review.
Adding a connector or MCP tool? See coworker/ for existing connector implementations to follow the established pattern.
Before opening a large PR, consider opening an issue first to discuss the approach - this avoids duplicate work and helps make sure the design fits the project's direction.
