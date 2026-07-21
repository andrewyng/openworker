# Email (IMAP/SMTP) connector — spec

Status: draft for review · Target: `platform/coworker/connectors/`

## Why

Today the only email path is the `gmail` connector, which asks the user to paste a raw
Google OAuth access token. Those tokens expire in ~1 hour and minting one requires a
Google Cloud project — it is effectively demo-only. Until our managed OAuth client is
verified (Calendar first; Gmail blocked on CASA), the practical way for a user to give
the agent their mailbox is the same one desktop mail clients have used for decades:
**IMAP/SMTP with an app password**. Setup is ~2 minutes for Gmail (myaccount.google.com/apppasswords)
and the credential never leaves the user's machine, matching our privacy story.

One connector covers Gmail, iCloud, Fastmail, and any custom IMAP server. Microsoft
killed basic-auth IMAP for Outlook.com, so Outlook stays on its Graph connector.

## Decisions

1. **New connector `email`** ("Email (IMAP)"). The existing `gmail`/`outlook` API
   connectors stay untouched; `email` becomes the recommended path for Gmail in docs.
   Later, the same connector gains an OAuth mode (Gmail IMAP supports XOAUTH2, reusing
   the PR #302 token manager) — app password and OAuth become two auth modes of one
   implementation.
2. **Stdlib only**: `imaplib` + `smtplib` + `email`. No new dependency (validated by
   Hermes' production gateway, which is pure stdlib). Tool functions are sync like the
   rest of `integration_tools.py`; one fresh connection per call, no pooling, no IDLE.
3. **Provider presets by address domain**, not a select field: the user enters address +
   app password; we infer hosts for known domains (`gmail.com`, `googlemail.com`,
   `icloud.com`/`me.com`/`mac.com`, `fastmail.com`). Optional advanced fields override
   hosts/ports for custom servers. Keeps the existing `Field` model (all text inputs) —
   no setup-wizard UI changes needed.
4. **Send and attachment download are approval-gated; v1 has no destructive tools**
   (no delete, no move, no flag changes). Pure reads (list/search/read) are
   approval-free like the other connectors' read tools; downloading an attachment is
   gated because it writes an untrusted file to disk.

## Descriptor

```python
ConnectorDescriptor(
    name="email",
    title="Email (IMAP)",
    icon="✉",
    blurb="Read, search, and send mail from any IMAP account — Gmail, iCloud, Fastmail, or custom.",
    auth="app_password",
    two_way=False,
    fields=[
        Field("address", "Email address", placeholder="you@gmail.com"),
        Field("app_password", "App password", secret=True,
              help="Gmail/iCloud: generate an app password (requires 2-step verification). Not your account password."),
        Field("display_name", "Display name", required=False,
              help="Shown as the From name on sent mail."),
        # Advanced — required only for domains we don't recognize:
        Field("imap_host", "IMAP host", required=False, placeholder="imap.example.com"),
        Field("imap_port", "IMAP port", required=False, placeholder="993"),
        Field("smtp_host", "SMTP host", required=False, placeholder="smtp.example.com"),
        Field("smtp_port", "SMTP port", required=False, placeholder="587"),
    ],
    instructions=[
        "Gmail: turn on 2-Step Verification, then create an app password at myaccount.google.com/apppasswords.",
        "iCloud: generate an app-specific password at account.apple.com → Sign-In and Security.",
        "Enter your address and the app password below. For Gmail, iCloud, and Fastmail the servers are detected automatically; otherwise fill in the IMAP/SMTP hosts.",
        "Note: Google Workspace and Microsoft 365 accounts often have IMAP or app passwords disabled by the org admin.",
    ],
    validate=_validate_email,
)
```

`_validate_email`: resolve preset → IMAP4_SSL login + `SELECT INBOX` (read path) and
SMTP STARTTLS login (send path), each with a 15s timeout; identity returned as
`"you@gmail.com · INBOX: 1,234 messages"`. Both must pass — a Gmail account with IMAP
disabled fails here with an actionable message, not at first tool call.

Secrets profile `email:default`:
`{address, app_password, display_name?, imap_host?, imap_port?, smtp_host?, smtp_port?}`
— stored exactly as entered; preset resolution happens at use time so we can improve
preset data without migrating stored profiles.

### Presets

| domain | IMAP | SMTP | sent folder |
|---|---|---|---|
| gmail.com, googlemail.com | imap.gmail.com:993 | smtp.gmail.com:587 (STARTTLS) | `[Gmail]/Sent Mail` (auto-saved by Gmail — never APPEND) |
| icloud.com, me.com, mac.com | imap.mail.me.com:993 | smtp.mail.me.com:587 | `Sent Messages` |
| fastmail.com | imap.fastmail.com:993 | smtp.fastmail.com:465 (TLS) | `Sent` |
| other | from advanced fields | from advanced fields | best-effort `Sent` |

## Tool surface

New module `platform/coworker/connectors/email_tools.py`, wired into
`make_integration_tools` and `tool_defs.py` like the existing connectors
(`__coworker_schema__` + `__aisuite_tool_metadata__`, creds read from SecretStore at
execution time, never in prompts).

| tool | kind | approval | params | returns |
|---|---|---|---|---|
| `email_list_folders` | read | no | — | folder names + message counts |
| `email_search` | read | no | `folder="INBOX"`, `from_address`, `to_address`, `subject`, `text`, `since`, `before` (YYYY-MM-DD), `unread_only`, `max_results` (default 10, cap 25) | newest-first envelopes: `{uid, date, from, to, subject, unread, has_attachments}` (no body snippet in v1 — it would need a per-message body fetch) |
| `email_read` | read | no | `uid`, `folder="INBOX"` | decoded headers, text body (text/plain preferred, HTML stripped as fallback, truncated at 20k chars), attachment list (filename, size, content type — not content) |
| `email_download_attachment` | read | **yes** | `uid`, `filename`, `folder` | saves into the session scratch dir only; returns the saved path (shows up in the artifact viewer). Approval-gated: attachments are untrusted files landing on disk — the card shows filename, size, and sender |
| `email_send` | write | **yes** | `to`, `subject`, `body`, `cc=""`, `bcc=""`, `reply_to_uid?` + `reply_to_folder?`, `attachments?` (paths within granted roots/scratch) | `{ok, message_id}` |

Search builds IMAP `UID SEARCH` criteria (`FROM`, `TO`, `SUBJECT`, `TEXT`, `SINCE`,
`BEFORE`, `UNSEEN`) with `CHARSET UTF-8`; fetches envelopes via
`BODY.PEEK[HEADER.FIELDS (...)]` — **PEEK everywhere so we never flip the user's
unread flags**. `email_read` fetches `BODY.PEEK[]` and parses with stdlib `email`
(RFC 2047 header decoding; walk parts, prefer `text/plain`, strip HTML fallback,
skip attachments).

`email_send` builds an `EmailMessage` (`From` = display name + address, `Date`,
generated `Message-ID`); when `reply_to_uid` is given, fetches the original's
`Message-ID`/`Subject` first and sets `In-Reply-To`, `References`, and `Re:` subject
so replies thread correctly. Sends via SMTP STARTTLS (or implicit TLS on port 465).
The approval card shows recipient, subject, and full body. v1 does not APPEND a copy
to the Sent folder (Gmail does it server-side; other providers documented as a
follow-up) — deliberate, to keep send single-shot and avoid Hermes' documented
footgun where a failed post-send step makes callers retry SMTP and double-send.

Failure messages are actionable: auth failure on a Gmail preset says "check that
2-Step Verification is on and this is an app password, not your account password."

## Testing

- Constructors are injectable (`imap_factory=imaplib.IMAP4_SSL`, `smtp_factory=...`)
  mirroring the providers' `client=` pattern; unit tests use recording fakes — no
  network, no SDK.
- Unit coverage: preset resolution + advanced-field override; search-criteria builder
  (dates, quoting, unread); envelope/header decoding (RFC 2047, malformed charsets);
  body extraction (multipart, HTML fallback, truncation); reply threading headers;
  attachment path confinement to scratch dir; send approval metadata.
- Live smoke script (`/tmp/test_email_connector.py`) against a real Gmail app
  password: list folders, search newest 5, read one, send-to-self round-trip.

## Privacy notes (for docs/quickstart)

- The app password is stored in the local SecretStore and used only to connect to the
  user's own mail server; it is never sent to any LLM provider or to us.
- Email *content* the agent reads does go to the configured model provider as context —
  same as any file the agent reads. Say this plainly in the quickstart.
- An app password grants full mail access; recommend revoking it from the provider's
  security page if the user stops using OpenCoworker.

## Out of scope (v1)

- Delete / move / flag tools (destructive; revisit with explicit per-tool opt-in).
- IMAP IDLE / inbound email-as-channel (future MyHelper inbound gateway can reuse the
  same profile + preset data).
- XOAUTH2 auth mode (follow-up; reuses PR #302's token manager once merged).
- Microsoft personal accounts (basic auth removed by Microsoft; Graph connector covers it).
- APPEND-to-Sent for non-Gmail providers.
