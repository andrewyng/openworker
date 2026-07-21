# Google Docs & Sheets connector — by-URL reads, tiered Sheets writes

Status: agreed 2026-07-10 (Rohit + agent). Motivated by the competitor
use-case analysis (ocw-context `docs/competitor-use-cases.md`): Google
docs/spreadsheets appear in 6 of 8 flagship workflows, always as "THIS doc /
THIS spreadsheet" — a pasted link, never a Drive-wide search.

## Decisions (locked)

- **One combined connector** `google_docs` (title: "Google Docs & Sheets").
  Both scope sets are Google **sensitive** tier, so combining costs nothing on
  the verification ladder — the gmail/google_calendar split existed to keep
  Calendar out of Gmail's *restricted*/CASA bucket, which doesn't apply here.
  One consent per Google account instead of two.
- **Consent tiers, HubSpot-style** (chosen at connect time, named tiers only):
  - `read` (default): `documents.readonly` + `spreadsheets.readonly`
  - `write`: `documents.readonly` + `spreadsheets` (full) — enables
    create-a-tracker, update-cells, append-rows. Docs stay read-only in both.
- **No Drive scope, no Drive search.** Documents are addressed by URL or id.
  Drive-wide search (`drive.readonly`, RESTRICTED → CASA) is wave 2, bundled
  with Gmail's CASA whenever that happens.
- **Multi-account from day one** via the generic accounts layer
  (CONNECTORS-BATCH2-SPEC §1): `account_field="@identity"` — the account key
  is the Google email (broker sends it as `account` from the id_token).

## Non-goals (wave 1)

- Docs WRITE (create/edit Google Docs) — briefs land as local artifacts or
  Notion pages; revisit on demand (`documents` scope is also sensitive, so
  it's an easy later add).
- Drive listing/search, Slides, Forms.
- Per-document privacy filters (nothing to filter without search).

## Broker (ocw-connect)

- `google` provider `connector_scopes` gains
  `"google_docs": ["documents.readonly", "spreadsheets.readonly"]`
  (full URLs). The existing `google_drive` stub stays for wave 2.
- **Model extension**: access tiers must be PER CONNECTOR on multi-connector
  providers — today `access_scopes` is provider-level (fine for HubSpot, a
  single-connector provider; wrong for google, where a `write` tier must not
  leak into gmail/calendar consents). Add
  `connector_access_scopes: dict[connector, dict[tier, list[scope]]]`;
  `start` resolves connector tier first, then provider-level (back-compat).
  For `google_docs`: `write` REPLACES `spreadsheets.readonly` with
  `spreadsheets` (not additive — avoid redundant scope pairs).
- Callback: nothing new — google identity extraction (email from id_token)
  already covers it; `account_id` already rides the loopback POST.
- **Prereq (Rohit)**: add the two sensitive scopes to the OAuth consent
  screen's scope list (same client, Testing mode: test users + 7-day refresh
  as usual). Publishing later = standard verification, no CASA.

## Desktop

- Descriptor `google_docs`: `managed=True`, `account_field="@identity"`,
  brand color `#4285f4`; manual field = `access_token` paste (same shape as
  gmail's — rarely used but keeps the local-only path alive). Validator:
  `GET https://www.googleapis.com/oauth2/v2/userinfo` → email.
- `PROVIDER_FOR_CONNECTOR["google_docs"] = "google"`. Managed connect passes
  `access` tier through `begin_managed_connect` (HubSpot precedent); the
  granted scope string is stored on the profile → connector_list exposes
  `access: "read" | "write"` per account (badge in the GUI, like HubSpot).
- **URL/id parsing** (shared helper): accepts full URLs
  (`docs.google.com/document/d/<id>/…`, `docs.google.com/spreadsheets/d/<id>/…`)
  or bare ids; the doc-vs-sheet kind comes from the URL path when present and
  is validated against the tool being called.

### Tools (all with `account` param + `_acct_result` stamping)

Read tier:
- `gdoc_read(url_or_id, account)` — Docs API `documents.get`, body flattened
  to readable text: headings prefixed (#/##), list items bulleted, table rows
  as `cell | cell | cell` lines. Returns `{title, text, truncated}` (cap
  ~50k chars).
- `gsheet_read(url_or_id, range="", account)` — no range: return
  `{title, sheets:[{name, rows, cols}]}` + the FIRST sheet's used range
  values; with range (`Sheet1!A1:D50` or just `Sheet1`): `values.get`.
  Values come back as a compact rows array; cap ~2k cells per call
  (ask for a narrower range beyond that).

Write tier (approval-gated, only usable when the account's grant includes
`spreadsheets`; a read-tier account gets a clear "connected read-only —
reconnect with write access" error):
- `gsheet_update_cells(url_or_id, range, rows_json, account)` —
  `values.update` (RAW input option; rows_json = JSON array of arrays).
- `gsheet_append_rows(url_or_id, range, rows_json, account)` — `values.append`.
- `gsheet_create(title, rows_json="", account)` — `spreadsheets.create`
  (+ optional initial values); returns the new sheet's URL.

tool_defs: 2 read + 3 write entries; write tools carry the tier note in
their descriptions so the model self-selects correctly.

## GUI

- **AccountsDetail already covers it** (generic accounts page) with one
  addition: an `access` badge per account row (`read-only` tag when the
  grant lacks `spreadsheets`) — same affordance as HubSpot's portal rows.
- Add-modal: One click | Manual pills with the read/write radio on the
  one-click pane — generalize `HubSpotOneClick` into the access-tier variant
  of `GenericOneClick` instead of a third bespoke pane.
- Row blurb: "Read docs and sheets by link; update sheets with approval."

## Tests

- URL/id parser (doc URLs, sheet URLs, bare ids, gid fragments, rejects).
- Doc flattener (headings/lists/tables → text) on a canned documents.get
  payload; truncation.
- Sheet read: no-range metadata+first-sheet shape; ranged values; cell cap.
- Write tools: routing + approval pinned + read-only-grant error path.
- Broker: per-connector access tier resolution (google_docs write ≠ gmail
  consent pollution), scope replacement not addition.
- e2e: accounts page reuse (access badge render); modal radio → connect body.
- Live drill (together): connect read on account A + write on account B,
  run the training-followups flow against a real Sheet, verify the read-only
  account refuses writes and approvals name the email.

## Build order

1. Broker: per-connector access tiers + google_docs scopes (+tests).
2. Desktop: descriptor/provider map/URL parser/read tools (+tests).
3. Desktop: write tools + tier gating (+tests).
4. GUI: access badge + one-click radio generalization (+e2e), visual review.
5. Rohit: consent-screen scopes; deploy (terraform approval); joint drills.

## Open questions (defaulted)

- Docs tabs (new Docs API tab structure): wave 1 flattens all tabs in order.
- `gsheet_create` lands in My Drive root (no drive.file scope to place it in
  folders) — acceptable; the tool returns the URL.
- CSV in/out helpers deferred; rows_json is enough for the model.
