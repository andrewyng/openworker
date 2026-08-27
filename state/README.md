# state/

This directory is **committed on purpose**. It is what makes a clone of this
repo a working OpenWorker rather than an empty one.

None of what makes an install *yours* is code. A fresh clone without this
starts with no personas, empty memory, no progress and an empty rail — which
looks like a broken build and is really an unpopulated one.

| path | carries |
|---|---|
| `coworker/personas.json`, `personas-installed/` | which personas exist and are surfaced |
| `coworker/persona_connections.json` | their MCP wiring |
| `coworker/coworker.db.sql` | threads, plans, progress, memories |
| `coworker/automation.db.sql` | scheduled automations |
| `coworker/conversations/` | conversation history |
| `coworker/mcp.json`, `prefs.json`, `config.toml` | MCP servers, model binding, settings |
| `knowledge/` | the brain — FOCUS, ingest, reports, threads |

## Use

```bash
./scripts/state-sync.sh push    # live install -> state/   (then commit & push)
./scripts/state-sync.sh pull    # state/ -> live install
```

## Why the databases are `.sql`, not `.db`

Two reasons, both load-bearing.

**Git.** A binary `.db` becomes a fresh multi-megabyte object on every sync and
never delta-compresses. A `.dump` is text: it diffs, and it packs.

**Correctness.** The live stores run SQLite in WAL mode, so recent writes sit in
the `-wal`, not the `.db`. Measured here mid-migration: `automation.db` was
236 KB against a **4.0 MB** WAL. Copying the `.db` alone would have carried a
fraction of the automations and given no sign anything was missing. `.dump`
reads through the WAL; a file copy does not. `pull` rebuilds each database from
SQL and runs `PRAGMA integrity_check` before accepting it.

## What is never here

`secrets.json`, `openworker.env`, `sidecar-*.token` — credentials and
per-install tokens. `.gitignore` carries a matching guard so a stray
`git add -A` cannot sweep them in, and `push` refuses to finish if anything
secret-shaped lands here.

## The model binding

`prefs.json` names a provider and a model, and two boxes do not serve the same
ones — evo-x2 is ollama, the DGX Spark is vLLM. On `pull`, a box that already
has its own binding **keeps it**. A fresh box is told, loudly, which binding it
inherited and how to find the right one:

```bash
curl -s localhost:8000/v1/models     # vLLM
curl -s localhost:11434/v1/models    # ollama
```
