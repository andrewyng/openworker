# Optional Windows computer-use tool

The optional `computer-tools` crate registers `computer.use`. The custom native
tool host links it; the default SDK host does not acquire desktop access. There is no JavaScript
REPL, Python dependency, video recorder or general shell API. GUI input can still
operate powerful applications: this is **not a sandbox**, process allowlist or
comprehensive detector of consequential actions. Use an isolated test desktop.

## App integration

```rust,ignore
computer_tools::register(&mut registry)?;
```

Add `computer.use` to the coworker's version-3 `tools.json`. Its model alias is
`computer__use`. Discovery exposes a structured action schema; no control-plane
method is advertised to the model. All actions remain External. The normal
Ask/auto-review/Bypass policy evaluates each exact call, including its arguments.
Auto-review cannot enable screen access. Bypass bypasses tool approvals, not the
app's display/input capability lease.

The user may choose **Allow for this session** on a Computer Use approval. This
persists one grant covering all actions. Subsequent calls skip repeated prompts,
but still require the separate live capability lease and all observation/input
guards. The app's Session permissions list can revoke the grant. Restart preserves
the grant but requires fresh screen-sharing/input consent.

After a real user opts in, the trusted app sends:

```json
{"jsonrpc":"2.0","id":1,"method":"tool/control","params":{"session_id":"s_...","tool":"computer.use","request":{"action":"enable","share_with_model":true,"allow_input":false}}}
```

Set `allow_input:true` only after a separate input-control opt-in. `status` and
`disable` requests use only `{"action":"status"}` or `{"action":"disable"}`.
The app must not expose arbitrary control requests to model-generated web content
or forward a model's request as evidence of human consent. Trusted stdio callers
can grant access; this is not cryptographic proof that a human clicked a button.
The registry's optional `Tool::control`, `revoke`, `turn_finished`,
`model_context`, and `images_allowed` hooks are
tools-SDK extension points, not a new stable embedded-harness SDK.

One conversation can hold access in a runtime process. Lease: five minutes by default,
primary display only, memory-only; no automatic renewal. The app may supply an
optional integer `duration_seconds` on `enable` (60–86,400 seconds). Omission
defaults to 300 seconds; invalid values reject without replacing the active
lease. An app can offer a custom whole-minute field (1–1,440), prefilled with 5 each
time its consent dialog opens. This changes the lease deadline, not permission
scope or the separate five-minute ephemeral-image retention limit. Successful text-only
turns preserve unused consent without extending its deadline. Once a computer
action attempts to use that consent, successful turn completion revokes it.
Restart, cancellation, failure, app Stop and Ctrl+Alt+Escape also revoke it. The keyboard
shortcut revokes further capture/input; it does not cancel an already transmitted
model request. An app's Stop action should also send `turn/cancel`. A different runtime process is
a separate trust domain, not globally coordinated by this lease.

Each provider attempt gets fresh model-safe tool access facts, including whether
screen access/input is enabled and how long remains. These override stale access
errors in conversation history but do not authorize actions; normal approval and
execution-time capability checks still apply. Status also reports an off reason
(expired, task_finished, task_failed, stopped, emergency_stop, or not_enabled).
Only the most recent ended lease's reason is retained in memory, not a durable
history. Apps should show that reason and actual structured tool errors. A generic
control-update journal event still omits arbitrary private control payloads.

## Observe → act → verify

```json
{"action":"observe"}
{"action":"click","observation_id":"obs_...","x":120,"y":150}
{"action":"observe"}
{"action":"type","observation_id":"obs_...","text":"Hello"}
{"action":"observe"}
{"action":"keypress","observation_id":"obs_...","keys":["CTRL","A"]}
{"action":"observe"}
{"action":"scroll","observation_id":"obs_...","direction":"down","amount":2}
```

Each approved call provides a cancellable three-second countdown. After approving
in the host app, switch back to the intended app. Observation uses a primary-screen GDI
snapshot, JPEG-encoded with a maximum dimension of 1280 pixels. Coordinates refer
to that returned image, not physical display pixels. A screenshot includes all
visible windows on that display; this version does not crop to a selected app.

Inputs require a latest, unconsumed observation no older than 30 seconds and the
same foreground window/display geometry. Clicks must hit that foreground window;
scrolling requires the pointer over it. No automatic focus stealing. A changed
screen within the same window is still possible—these guards reduce stale-input
risk, not eliminate it. Held modifier keys/mouse buttons block injection. Typing
is bounded to 256 UTF-16 units, with no control characters. Key combinations are
allowlisted; no Win shortcuts, drags, arbitrary scripts or held-key API.

An input receipt confirms Windows accepted input events, **not** that the app
completed the intended action. Partial input errors are uncertain effects and
must not be retried automatically. Observe to verify. Windows UIPI prevents input
into higher-integrity applications; never elevate to work around a failure.
Secure desktops, protected content and noninteractive/headless Windows services
are unsupported. Images may omit protected content. Accessibility trees and
multi-monitor/window selection are follow-up work.

## Image transport and retention

Native tools may return `ToolOutput.images` alongside JSON `data`. This slice
accepts exactly one bounded JPEG/PNG (at most 768,000 base64 bytes). The harness
keeps only the latest native image per session, at most four image-bearing
sessions. The image owner can revoke forwarding with `images_allowed`; it is
checked before **each provider attempt**, including retries.

The journal, replay, UI snapshots and compaction see only image metadata. At
request time, the current image is substituted into Responses function output
as `input_text` + `input_image` blocks. Old/missing/restarted images become explicit
unavailable-image notices. Images are no longer eligible after five minutes;
cache cleanup occurs on access, app disable, cancel or terminal cleanup. Memory
is not securely zeroized and OS paging/crash dumps are outside this retention
claim. No screenshot file or image bytes are deliberately persisted by the harness.
Model-generated descriptions and typed text **are** normal durable conversation
data. Provider-side retention is separate; requests use the existing `store:false`.

This implementation covers native image outputs. It does not yet add generic
MCP image conversion, user image attachments, video analysis or a durable artifact
download API. Do not send image data through `ToolOutput::json`, progress messages
or logs; those are durable paths. Native partner code shares process trust.

Sources: [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling),
[Windows SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).
