# Using MCP servers

OpenWorker can use any tool reachable over [MCP](https://modelcontextprotocol.io/)
(Model Context Protocol). Each configured server's tools join the agent's toolbox as
`mcp__<server>__<tool>`, and they go through the same approval flow as every other
consequential tool.

## Where configuration lives

| File | Scope | Applies |
|---|---|---|
| `<state-dir>/mcp.json` | Global — every session | always |
| `<workspace>/.coworker/mcp.json` | One project | only after you trust that workspace |

`<state-dir>` is `~/.config/coworker` on macOS/Linux and `%APPDATA%\coworker` on
Windows (`$COWORKER_STATE_DIR` overrides it anywhere).

Two rules keep this safe:

- **Untrusted workspaces are never read.** A stdio server is a process that starts when
  a session opens, so a repository you merely cloned must not get to define one. The
  workspace file only takes effect after you trust that folder (the app prompts; the
  same gate covers a repository's `allowed_commands`).
- **Global wins on a name clash.** Even a trusted repository cannot silently redefine a
  server you configured globally by reusing its name.

The app's MCP page edits the global file. Servers that back a built-in connector
(for example Jira or monday.com) are managed from the Integrations page instead and do
not appear on the MCP page.

## File format

The standard `mcpServers` JSON — a config you already use with Claude Desktop, Cursor,
or Codex can be pasted as-is:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/notes"]
    },
    "internal-api": {
      "url": "https://mcp.example.com/mcp",
      "headers": {"Authorization": "Bearer ${INTERNAL_API_TOKEN}"},
      "include_tools": ["search", "read_doc"],
      "requires_approval": false
    },
    "issue-tracker": {
      "url": "https://mcp.tracker.example/mcp",
      "auth": "oauth"
    }
  }
}
```

Per-server fields:

- `command`, `args`, `env`, `cwd` — a stdio server, spawned locally.
- `url`, `headers` — an HTTP server (`streamable-http`/`sse`; `type` may name the
  transport explicitly, otherwise a `url` implies HTTP).
- `enabled` — set `false` to keep a server configured but inert.
- `include_tools` / `exclude_tools` — limit which of the server's tools are offered.
- `requires_approval` — defaults to `true`: every call asks first. Set `false` only for
  a server you trust to act unprompted; you can also relax specific tools by pattern in
  `<state-dir>/risk_overrides.json`.
- `auth: "oauth"` — browser sign-in for HTTP servers (below).

`${VAR}` references in `command`, `args`, `env`, `url`, and `headers` are resolved at
load time from your environment and `<state-dir>/.env` — so a token never has to be
written into `mcp.json` itself.

## OAuth servers

Servers declaring `auth: "oauth"` use OAuth 2.1 with PKCE and Dynamic Client
Registration: no client id to pre-register, and the broker holds nothing — the flow is
entirely between your machine and the server. Connect from the MCP page; a browser
window completes the sign-in, and tokens land in OpenWorker's local secret store,
never in `mcp.json`.

A session will never launch a browser on its own: if a server needs (re-)auth at turn
time it is skipped and marked on the MCP page, and the session simply runs without its
tools until you reconnect. Signing out forgets both the tokens and the dynamic client
registration.

## Statuses on the MCP page

`connected` (reachable, tools listed) · `authorizing` (browser flow in progress) ·
`needs_auth` (reconnect to sign in again) · `configured` (defined, not yet probed) ·
`disabled` (`enabled: false`). Connection errors are shown per server so a dark server
says why.

## For API users

The sidecar exposes the same operations over REST (token header required):
`GET/POST /v1/mcp`, `PATCH/DELETE /v1/mcp/{name}`, `GET /v1/mcp/{name}/tools`,
`POST /v1/mcp/{name}/connect`, `POST /v1/mcp/{name}/signout`, `POST /v1/mcp/reload`.
These edit the **global** file only.
