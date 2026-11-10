# OpenWorker Console

Optional read-only operations UI, using the reference harness HTTP API. No
database access, agent loop, tool authority or telemetry dependency. Buildless
HTML/CSS/JavaScript plus a Node 20+ server; no npm runtime dependencies.

Use the [local deployment reference](../../deploy/reference/README.md) for a
working PostgreSQL/S3/Grafana/OpenShell setup, or start independently:

```json
{
  "port": 54340,
  "apis": ["http://127.0.0.1:58181", "http://127.0.0.1:58182"],
  "api_token_file": "/private/harness-api-token",
  "access_key_file": "/private/distinct-console-access-key",
  "grafana_url": "http://127.0.0.1:53000/d/openworker-harness"
}
```

Generate a distinct cryptographically random console key (32–256 URL-safe
characters), store it in a private file, and run
`node harness/console/server.mjs /private/console.json`. `grafana_url` is optional.
Sign in with the console key, not the API token. Do not place tokens in URLs.

The UI lists sessions, shows current turn/pending decisions and bounded recent
messages, follows durable SSE events and downloads registered artifacts. It
renders model/user strings as text, never Markdown/HTML. Activity retains only
the latest 80 events in browser memory; full session history is not downloaded.
Snapshot cursors and interaction IDs are authoritative, not telemetry charts.
Pending decisions are inspected here but answered through the application/CLI.

## Boundary

The server binds `127.0.0.1`, checks Host/Origin/fetch-site, rate-limits local login,
issues HttpOnly/SameSite=Strict eight-hour cookies and enforces a GET-only API
allowlist. It does not forward browser cookies/headers/credentials upstream.
The harness bearer remains on the server. Only pre-response GET failures can
fail over; streams reconnect with durable IDs. Downloads preserve attachment
headers; CSP/no-store/nosniff apply. UI/server restart requires login again.

This is a trusted local observer UI, not SSO or physical tenant isolation. Its
server requires a real observer credential, verifies `/auth/me` on available API
nodes at startup, and refuses operator/admin keys. The API enforces that key's
session scope and read-only role independently of this proxy. Provision it using
the [authorization guide](../server-reference/AUTHORIZATION.md). Production still
needs TLS and appropriate user login/session mapping, or its own UI.
Do not publish the local server via a tunnel or change its binding casually.

## API addition

`GET /sessions?limit=50&after=SESSION_ID` returns `{sessions,next_after}` newest
creation first. Limit 1–100; `after` is an existing session ID, not an event
cursor. Summaries contain operational fields/pending counts, not prompts,
arguments, credential configuration or history. New sessions arriving during
paging appear on refresh, not in a frozen list snapshot. Selected session data
uses existing coherent `GET /sessions/:id`; events and artifacts use their
existing endpoints. No protocol/schema change to local SDK/stdio/Desktop.

## Tests

`node --test harness/console/server.test.mjs` tests auth, mutation rejection,
read failover, URL/Origin/Host boundary and browser credential separation.
`browser-check.mjs` is optional Playwright QA against actual qualification data.
The Sites local-only
workflow guided responsive design and visual verification; nothing is hosted.
