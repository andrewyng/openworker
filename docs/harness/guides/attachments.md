# Attach images, PDFs and text files

Attach a screenshot, a PDF report or a UTF-8 text file to a user message. Files
are model inputs: attaching a file does not place it in a sandbox, grant a tool
permission or share it with a child agent. Choose a model that accepts the formats
you use. Provider request/context limits still apply.

npm installs the compatible native runtime automatically. A remote service must
support attachments; an incompatible host rejects the request rather than dropping files.

## Add files after a message

```typescript
import { Attachment } from '@openworkerai/harness-sdk';

const screen = await Attachment.fromFile('./screen.png');
const report = await Attachment.fromFile('./report.pdf');
const result = await session.run('Compare this screen with the report.', [screen, report]);
```

A single attachment works too: `session.run('Explain this.', report)`. Existing
`session.run(text, options)` calls keep their meaning. Pass turn options as the
third argument when using the attachment convenience form.

`Attachment.fromFile` reads on the caller's machine. For a browser upload, send
bytes through your authenticated application backend, check the user's ownership
and size limits there, then construct an attachment in the backend:

```typescript
const report = Attachment.fromBytes({
  name: 'report.pdf', mediaType: 'application/pdf', data: uploadedBytes,
});
```

The Node SDK runs in your server or desktop application's trusted process.
Browser and renderer code use your application's upload/API boundary.

## Interleave text and files

An ordered `content` array represents one user message. Text can appear between
files; order is preserved in storage and provider requests.

```typescript
const input = { content: [
  { type: 'text' as const, text: 'Look at this screen.' }, screen,
  { type: 'text' as const, text: 'Compare it with this report.' }, report,
  { type: 'document' as const, name: 'notes.md', source: {
    type: 'text' as const, mediaType: 'text/plain' as const,
    data: 'The report covers the previous release.',
  } },
] };
const run = await session.send(input, { submissionId: 'review-42' });
for await (const event of run.events()) { /* update your UI */ }
const result = await run.result();
```

`run`, `send`, `runWith` and `runStreamed` accept structured input. `runStreamed`
also accepts the text-plus-attachments convenience form. An explicit image or
document block can use a base64 source:

```typescript
{ type: 'image', name: 'screen.png', source: {
  type: 'base64', mediaType: 'image/png', data: imageBase64,
} }
```

Use `{type: 'attachment', id}` as the source only for an already committed
OpenWorker attachment **in this session**. These are not OpenAI or Anthropic file
IDs. The harness rechecks ownership and type at admission; a label cannot change
the stored file. URLs and provider-owned file IDs are outside this initial API.

## Rust

```rust
use openworker_sdk::{Attachment, InputContent, UserInput};

let report = Attachment::from_file("./report.pdf")?;
let input = UserInput::new(vec![
    InputContent::text("Read this report."),
    report.clone().into(),
    InputContent::text("Explain the main changes."),
]);
let result = session.run_input(input).await?;
// Convenience form:
let result = session.run_with_attachments("Explain this.", vec![report]).await?;
```

Use `send_input(input, TurnOptions)` for submission IDs and turn options, or
`run_input_streamed(input)` for journal events followed by `finish()`.

## Connect to a service

The same TypeScript input works with a connected service session:

```typescript
const run = await serviceSession.send(input, { idempotencyKey: 'review-42' });
```

Rust service sessions expose `send_input(input, limits, idempotency_key)`.
The SDK stages bounded chunks over authenticated HTTP, then submits references.
Each chunk is checked against the caller's session permissions. API replicas and
replacement workers share immutable bytes through the execution PostgreSQL
store; the observability database does not store those bytes.

## Formats, limits and recovery

| Input | Model representation |
| --- | --- |
| PNG, JPEG, static GIF, static WebP | Native image part |
| PDF | Original PDF document, without text extraction |
| UTF-8 text (`.txt`, `.md`, `.json`, `.csv`, source files) | Named text at the same content position |

The initial SDK limits are **8 MiB per file**, **16 MiB of files per message**,
**32 content blocks** and **32,000 bytes of inline text**. A session reserves at
most **128 files / 64 MiB** of stored files, including uploads awaiting acceptance. The host
checks media headers, UTF-8 and hashes; this is not malware scanning or a full
image/PDF decoder. Unsupported formats and animation are rejected. Provider/model
limits can be smaller; the SDK does not promise that every model supports PDF or
vision input. Gemini-native input is not part of this qualification.

The stdio frame limit remains 1 MiB. The SDK hides 256 KiB raw-byte upload chunks,
checksum validation and commit. Uploads can be repeated with identical bytes;
conflicting bytes or metadata are rejected. Reuse the same submission ID and
content to reconcile a lost turn receipt. Changing a file, its label, order or
text requires a new submission ID.

Accepted history stores immutable references. Bytes remain in the session's
private execution store for restart/recovery and provider switching. Compaction
retains text and reference descriptions in its summary input while preserving
the complete original history and bytes. It may omit older file bodies from the
current model context. The existing `initialHistory` import remains text and
completed tool calls/results; it does not copy another session's files.

Unaccepted uploads, including sealed files, expire after 24 hours and are cleaned
on the next upload in that session. Accepted references remain for the session's
retention lifetime. Retrying an expired upload stages the same bytes again.
Cancellation while staging prevents turn submission; it can leave an expiring
upload. After turn acceptance, stopping a wait does not cancel agent execution.

## Guardrails and privacy

User-input checks cover each inline text block and decoded text file. A redaction
creates a new immutable file reference; the accepted message points to the checked
text. Model-input checks also see decoded text documents. Existing guardrail size
limits apply (currently 128,000 bytes per inspected input); oversized required
checks fail closed. Large text attachments without those checks are still subject
to the selected model's context limit.

Text-only guardrails cannot inspect pixels or PDF pages. Binary attachments are
rejected when a configured user-input or model-input policy requires inspection.
A model-input policy added on a later turn also covers binary files still present
in that turn's model context.
There is no implicit OCR, extraction or multimodal safety scanner. Scan those
files in your application or use text inputs when that policy is required.

Ordinary journal events and snapshots contain references, not file bodies or
base64. Binary content is withheld even from full-content model telemetry. Text
files become model text, so explicit full-content capture can include checked
text; keep capture disabled or redacted for sensitive documents.

## Upgrade an existing store

Back up persistent stores before upgrading. Follow [storage upgrades](upgrades.md)
for schema compatibility and PostgreSQL migration steps. Use compatible SDK,
service and worker builds so queued attachments cannot become text-only turns.

For a lightweight Rust build, disable default features and enable only what you
need. Attachment admission/upload can be excluded with the `attachments` feature;
no PDF renderer, OCR engine or office converter is linked by this feature.
