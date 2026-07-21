# Connectors Batch 2 — Notion, Attio (managed OAuth) + PostHog, Mixpanel, Amplitude, Apollo, Hunter (manual) — with a generic multi-account layer

Status: agreed 2026-07-10 (Rohit + agent). Supersedes nothing; extends M3.6.

## Goals

1. Seven new connectors: **Notion + Attio** with one-click managed OAuth via the
   ocw-connect broker (manual token paste also available, as always), and
   **PostHog, Mixpanel, Amplitude, Apollo, Hunter** as manual API-key connectors.
2. **Multi-account from day one, for all seven.** Users routinely hold multiple
   accounts of the same SaaS (two Notion workspaces, several PostHog projects).
   We already do this bespoke for Slack (workspaces), Gmail/GCal (accounts), and
   HubSpot (portals) — this batch introduces the GENERIC account layer so we
   never write a bespoke module again.
3. Every tool result and approval card names the account it touched.

## Non-goals

- Migrating gmail/gcal/hubspot/slack onto the generic layer (later refactor;
  their shipped behavior must not churn in the same batch that adds 7 connectors).
- Write tools beyond the minimal useful set (this batch is read-heavy).
- Webhooks/relay inbound for any of the seven (outbound/read only).
- Per-account privacy filters (Gmail-style "never show agents") — later if asked.

## 1. Generic account layer — `coworker/connectors/accounts.py`

Same shape as `gcal_accounts.py`, parameterized by connector name:

- Profiles at `<connector>:account:<account_id>`; `<connector>:default` is a
  POINTER ONLY: `{"default_account": <id>}` (plus connector-wide settings like
  `enabled`). Never tokens.
- `list_accounts(secrets, connector) -> [(id, profile)]`
- `default_account(secrets, connector) -> id` (pointer, else sole account)
- `resolve(secrets, connector, account="") -> (profile_key, profile)` — explicit
  account, else default, else the sole account; `{}` when nothing connected.
- `add_account(secrets, connector, account_id, fields)` — used by both manual
  connect and the managed OAuth callback; sets the default pointer if first.
- `set_default`, `disconnect_account` (last disconnect deletes the pointer).
- **Legacy migration**: a token-bearing `<connector>:default` (from any older
  build) is treated as one account and lazily rewritten on first touch —
  mirrors the shipped gmail/gcal migration; no user action.

**Account id per connector** (stable, human-meaningful):

| connector | account_id | shown as |
|---|---|---|
| notion | workspace_id (from OAuth token response) | workspace_name |
| attio | workspace_id (from `GET /v2/self`) | workspace name |
| posthog | project_id | `host · project` |
| mixpanel | project_id | project_id |
| amplitude | api_key last 6 | `key …abc123` |
| apollo | validator identity (account email) | email |
| hunter | validator identity (account email) | email |

Descriptor gains `account_field: str = ""` — the creds field (or `"@identity"`
sentinel = use ValidationResult.identity) that names the account. Non-empty
`account_field` marks the connector as account-patterned everywhere
(connect path, connector_list, server routes, GUI).

## 2. Desktop plumbing

- **Connect (manual)**: for account-patterned connectors, `connect_connector`
  validates, derives the account id, and writes `<name>:account:<id>` via
  `add_account` instead of overwriting `<name>:default`. Re-connecting the same
  id updates it; a different id adds a second account. Managed OAuth callback
  does the same with broker-returned identity.
- **connector_list**: generic branch for account-patterned connectors, same
  shape as gmail/gcal: `accounts: [{account, default, managed}]`,
  `connected = len(accounts) > 0`, `account` = default account (back-compat).
- **Routes** (generic, allowlisted to account-patterned connectors):
  - `POST /v1/connectors/{name}/accounts/{id}/disconnect` (+ best-effort
    cloud_disconnect for managed profiles)
  - `POST /v1/connectors/{name}/accounts/{id}/default`
  (gcal keeps its existing specific routes; no alias churn.)
- **Tools**: every tool gets `account: str = ""`; shared helper
  `_account_profile(secrets, connector, account, *fields)` (resolve →
  `ensure_fresh_connector_token(profile_key=…)` for managed → field check);
  every result passes through `_acct_result(account_id, result)` so approvals
  and transcripts name the account.

## 3. The seven connectors

### Notion (managed + manual)
- OAuth: authorize `https://api.notion.com/v1/oauth/authorize` (`owner=user`),
  token exchange with **HTTP Basic** (client_id:secret). Tokens are long-lived
  (no refresh flow). Token response carries `workspace_id`/`workspace_name` —
  the broker forwards them; desktop stores them on the account profile.
- Manual path: paste an internal-integration token; account id = workspace id
  from `GET /v1/users/me` (bot owner workspace).
- Tools: `notion_search(query, account)` (POST /v1/search),
  `notion_read_page(page_id, account)` (page + block children, text-flattened),
  `notion_query_database(database_id, filter_json, account)`,
  `notion_create_page(parent_id, title, markdown_body, account)` [write].
- Validator: `GET /v1/users/me`.

### Attio (managed + manual)
- OAuth: authorize `https://app.attio.com/authorize`, token
  `https://app.attio.com/oauth/token` (standard code exchange). Long-lived
  access token. Identity: `GET /v2/self` → workspace id + name.
- Manual path: workspace API key (same header).
- Tools: `attio_list_objects(account)` (GET /v2/objects),
  `attio_search_records(object, query, account)` (POST
  /v2/objects/{object}/records/query), `attio_get_record(object, record_id,
  account)`, `attio_create_note(parent_object, parent_record_id, title,
  content, account)` [write].
- Validator: `GET /v2/self`.

### PostHog (manual)
- Fields: `base_url` (default `https://us.posthog.com`), `api_key` (personal),
  `project_id`. Bearer auth.
- Tools: `posthog_query(hogql, account)` (POST /api/projects/{pid}/query,
  HogQLQuery kind), `posthog_list_insights(query, max_results, account)`.
- Validator: `GET /api/users/@me/` → email.

### Mixpanel (manual)
- Fields: service account `username` + `secret`, `project_id`. HTTP Basic.
- Tools: `mixpanel_segmentation(event, from_date, to_date, unit, where,
  account)` (GET mixpanel.com/api/query/segmentation),
  `mixpanel_top_events(max_results, account)` (GET /api/query/events/top).
- Validator: `GET https://mixpanel.com/api/app/me`.

### Amplitude (manual)
- Fields: `api_key`, `secret_key`. HTTP Basic. Dashboard REST API.
- Tools: `amplitude_active_users(start, end, metric, account)` (GET
  amplitude.com/api/2/users), `amplitude_event_totals(event_type, start, end,
  account)` (GET /api/2/events/segmentation).
- Validator: `GET /api/2/annotations`.

### Apollo (manual)
- Field: `api_key` (X-Api-Key header).
- Tools: `apollo_enrich_person(email, name, company_domain, account)` (POST
  /api/v1/people/match), `apollo_enrich_company(domain, account)` (GET
  /api/v1/organizations/enrich), `apollo_search_people(query, max_results,
  account)` (POST /api/v1/mixed_people/search).
- Validator: `GET /api/v1/auth/health`.

### Hunter (manual)
- Field: `api_key` (query param).
- Tools: `hunter_domain_search(domain, max_results, account)`,
  `hunter_find_email(domain, first_name, last_name, account)`,
  `hunter_verify_email(email, account)`.
- Validator: `GET /v2/account` → email.

All tools approval-gated (house default); `caps=[<connector>, "read"|"write"]`.
Writes in batch: `notion_create_page`, `attio_create_note` only.

## 4. Broker (ocw-connect)

- Two `Provider` entries: `notion` (Basic-auth token exchange, no refresh,
  forward `workspace_id`/`workspace_name`/`bot_id` in the callback result) and
  `attio` (standard exchange). No scope sets (both fix scopes at app level).
- Redirect URIs: `https://api.opencoworker.app/v1/oauth/{notion,attio}/callback`.
- Stateless as ever: tokens go to the desktop, nothing at rest.
- **Prereqs (Rohit)**: register the Notion public integration + Attio app,
  add client ids/secrets to the `ocw-connect/oauth-providers` secret
  (key names only in any transcript), approve terraform apply.

## 5. GUI

- **AccountsDetail** (generic detail page, registered for all seven): accounts
  group with Default badge / set-default / × disconnect-account, "＋ Add
  account" (managed → `connectManaged`; manual → the connector's field form),
  Tools disclosure, footer Disconnect-all. Modeled on CalendarDetail; replaces
  GenericDetail for account-patterned connectors.
- ConnectorsList row status shows `N accounts` when >1.
- Add-connection modal: Notion/Attio get One click | Manual pills (≥2 modes);
  the five manual ones keep the plain field form.
- Visual review in the dev app before PR (house rule).

## 6. Tests

- `tests/test_accounts.py`: generic layer — add/list/resolve/default/disconnect
  /legacy-migration, per connector-name isolation.
- `test_connectors.py`: routing tests for all new tools (fake `_request`),
  not-connected errors, account param → profile resolution, `_acct_result`
  stamping, write tools approval-pinned.
- `test_setup.py`-style: connector_list accounts branch; connect twice with
  different ids → two accounts, default stable.
- Server route tests: generic disconnect/default (allowlist rejects
  non-account connectors).
- Broker: provider config tests (authorize URL shape, Basic-auth exchange for
  notion), callback forwarding of workspace fields.
- e2e: accounts page spec (fixtures with two Notion accounts), add-account
  modal, default switch; connectors-list count row.
- Live drills (with ledger entries for anything caught): two Notion
  workspaces one-click; Attio workspace; each manual key validated; account
  disambiguation in approvals; dual-account tool calls.

## 7. Build order (each step lands green + committed)

- **Step 0** — accounts.py + descriptor `account_field` + connect-path +
  connector_list branch + generic routes + tests.
- **Step 1** — the five manual connectors on the pattern (descriptors,
  validators, tools, tool_defs) + tests.               [task #27]
- **Step 2** — broker notion/attio providers + tests; deploy after prereqs +
  explicit approval.                                    [task #28]
- **Step 3** — desktop notion/attio (descriptors managed=True, callback
  branches, tools) + tests.                             [task #29]
- **Step 4** — GUI AccountsDetail + modal pills + e2e; visual review.
- **Step 5** — live drills, ledger, then DMG rebuild (full build_dmg.sh,
  signed).                                              [task #30]

## Open questions (defaulted, flag to change)

- Attio/Notion write tools kept minimal (create page / note). More on ask.
- PostHog EU cloud → covered by `base_url`; self-hosted too.
- Amplitude EU residency zone (analytics.eu.amplitude.com) — not in v1.
