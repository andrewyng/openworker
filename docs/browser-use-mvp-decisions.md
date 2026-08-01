# Browser Use MVP — validated decisions

Status: implemented MVP baseline
Branch: `codex/browser-use-mvp`
Baseline: `origin/main` at `01b6f83b3927`

## Outcome

OpenWorker can ship a Codex-like browser agent and can remember website sign-ins across
conversations. The MVP should use Playwright's bundled full Chromium, native AI accessibility
snapshots, isolated browser contexts, an encrypted reusable authentication vault, and a
fail-closed egress proxy.

The existing `coworker/connectors/browser_automation.py` is a prototype, not the security or
lifecycle foundation for this feature. In particular, its process-global page, text/CSS
targeting, broad URL access, file upload, arbitrary screenshot path, and durable raw tool results
must not become the public Browser Use contract.

## Product contract

- Browser Use is attended-only in the MVP. Scheduled and unattended browser work is disabled.
- The browser appears in an in-app panel and is controlled through agent tools.
- The browser remains directly interactive while the agent works. Human and agent events share
  the page's natural event order; human input invalidates stale agent snapshots so the agent
  observes again instead of acting on an outdated target.
- Each active conversation gets an isolated browser context. Agent actions remain serialized;
  human events bypass that queue and may interleave naturally.
- Sign-in is performed by the user directly in the browser panel. Passwords are never exposed to
  the agent, tool transcript, audit log, or model provider.
- “Remember sign-ins on this device” is explicit and stores a named OpenWorker browser profile.
- A saved browser profile is separate from the user's normal browser and separate from
  conversations. Deleting a conversation does not sign the user out.
- “Clear browser data” closes contexts using that profile and destroys its saved authentication
  state.
- Browser observations are ephemeral. Conversation history and audit storage retain only
  redacted action metadata, never page contents or authentication data.

## Decisions

### 1. Browser runtime and packaging

Pin Python Playwright and its Chromium revision to one tested release, initially `1.61.x`. Launch
the full Chromium build in new-headless mode with `channel="chromium"`. Do not ship the separate
headless shell.

Build changes:

1. Change the browser extra from a floating lower bound to the exact tested Playwright release.
2. Install `.[bedrock,browser]` in release jobs.
3. Run `playwright install chromium --no-shell` on each target platform.
4. Bundle Playwright's Python package and Node driver in the PyInstaller sidecar.
5. Stage Chromium as an app resource outside the sidecar tree.
6. Set `PLAYWRIGHT_BROWSERS_PATH` explicitly when Tauri starts the sidecar.

On macOS, preserve the Chromium framework's symlinks and place it under
`OpenWorker.app/Contents/Frameworks`. Sign nested helpers and frameworks inside-out, then sign
the outer OpenWorker app, DMG, notarize, staple, and verify. Do not run the current sidecar
resource dereferencing logic over Chromium.

On Windows, build the native browser payload in the Windows job. Sign the sidecar, Playwright
driver, Chromium executables and DLLs, and the MSI/NSIS installers with one timestamped publisher
identity.

Evidence:

- Playwright officially supports PyInstaller and `PLAYWRIGHT_BROWSERS_PATH=0`:
  [PyInstaller instructions](https://playwright.dev/python/docs/library#pyinstaller).
- Browser versions are coupled to Playwright releases, and `--no-shell` avoids the extra headless
  shell payload: [Playwright browsers](https://playwright.dev/python/docs/browsers).
- Tauri supports custom macOS bundle files:
  [Tauri macOS bundle files](https://v2.tauri.app/distribute/macos-application-bundle/#adding-custom-files).
- Apple requires nested code to be signed inside-out:
  [Apple TN2206](https://developer.apple.com/library/archive/technotes/tn2206/).

Measured locally on Apple Silicon:

| Item | Measurement |
|---|---:|
| Existing OpenWorker sidecar | 121 MB installed |
| Existing DMG | 71 MB compressed |
| Frozen minimal Playwright driver | 127 MB / 39 MB gzip |
| Full Playwright Chromium | 344 MB on disk / 159 MB gzip |
| Expected browser-enabled DMG | approximately 260–280 MB |
| Expected added installed footprint | approximately 470 MB |
| Warm frozen-driver startup | 181 ms |
| Warm Chromium launch | 223–294 ms |

These numbers resolve feasibility and set an installer-size expectation. Release signing remains
a certification job, not an architecture question.

### 2. Browser and conversation isolation

Create a `BrowserRuntime` owned by `SessionManager`:

- one Playwright driver and Chromium process per OpenWorker process;
- one isolated context per active conversation;
- one serialized operation queue per context;
- opaque, server-generated conversation binding on every call;
- no model-supplied conversation or profile identifiers;
- one exclusive writer lease per saved browser profile.

The current global `_BROWSER` singleton must be removed. Browser read actions must not be
parallelized with navigation or other browser actions.

### 3. Durable cookies and signed-in sessions

The MVP browser profile is an encrypted Playwright authentication-state vault, not a shared
Chromium `user_data_dir`.

On a clean checkpoint, call:

```python
await context.storage_state(path=None, indexed_db=True)
```

Encrypt the resulting JSON with AES-256-GCM using a random per-installation key protected by
macOS Keychain or Windows DPAPI. Store ciphertext only under the application state directory,
write it with user-only permissions using atomic replace, and never expose that directory as a
workspace root. Decryption failure is fail-closed and prompts the user to sign in again.

Restore the vault only into a fresh isolated context that owns the profile lease. Checkpoint it
after successful state-changing actions, on idle eviction, and on clean shutdown.

Verified behavior:

| Browser state | Restored from authentication state |
|---|---:|
| Persistent cookies | Yes |
| Session cookies | Yes |
| `localStorage` | Yes |
| IndexedDB | Yes |
| `sessionStorage` | No |

The saved JSON contains bearer credentials in plaintext before encryption. File permissions
alone are not sufficient. A full persistent Chromium profile also leaves local-storage and
IndexedDB data readable in its LevelDB files, is much harder to encrypt safely, and permits only
one live owner. It is therefore deferred unless compatibility evaluation proves the narrower
vault insufficient.

Sites can still demand reauthentication because of expiry, server policy, `sessionStorage`-only
authentication, CAPTCHA, 2FA, hardware-backed credentials, or device binding. OpenWorker does
not bypass those controls; it hands the browser back to the user.

Evidence:

- [Playwright authentication state](https://playwright.dev/python/docs/auth)
- [Playwright persistent contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)
- [Apple Keychain](https://developer.apple.com/documentation/Security/keychain-services)
- [Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)

### 4. Exact element targeting

Use Playwright 1.61's native AI accessibility snapshot and `aria-ref` locator:

```python
snapshot = await page.aria_snapshot(mode="ai")
target = page.locator(f"aria-ref={ref}")
```

Every tab gets an opaque `tab_id`. Every actionable observation gets a new, monotonically
increasing `snapshot_id`. Every element action requires all three:

```json
{
  "tab_id": "tab_7",
  "snapshot_id": "snap_42",
  "ref": "f1e18"
}
```

Rules:

- reject refs from another tab or an older snapshot;
- do not fall back to CSS, text matching, `.first`, `.nth`, XPath, or coordinates;
- return a fresh snapshot after every action, navigation, popup, or dialog transition;
- make popup refs tab-scoped;
- return machine-readable `STALE_SNAPSHOT`, `REF_NOT_FOUND`, `TAB_NOT_FOUND`, `DIALOG_OPEN`,
  and `ACTION_TIMEOUT` errors;
- include the latest compact snapshot with a stale-target error so the agent can recover once.

Measured behavior:

- duplicate accessible names receive distinct refs;
- refs survive unrelated text updates;
- refs become stale when the target is remounted;
- open Shadow DOM and same- or cross-origin iframes work;
- closed Shadow DOM is not exposed;
- popup ref namespaces can collide unless tab-scoped;
- virtualized rows prove that `.first`/`.nth` can silently target the wrong element.

Playwright's own agent tooling uses this snapshot/ref workflow:
[AI snapshots](https://playwright.dev/python/docs/next/api/class-page#page-aria-snapshot) and
[agent snapshots](https://playwright.dev/agent-cli/snapshots).

### 5. Shared viewport, agent cursor, snapshots, and frame transport

Tauri cannot directly embed Playwright's Chromium process inside a React panel. Use Chromium's
CDP screencast as a remote viewport instead:

- an authenticated, conversation-bound binary WebSocket carries JPEG/WebP frames;
- a separate JSON channel carries viewport metadata, agent cursor events, local takeover input,
  load state, and errors;
- the React panel renders the frame into a letterboxed canvas or image surface;
- viewport metadata and the rendered content rectangle define one reversible transform between
  panel coordinates and Chromium CSS viewport coordinates;
- user input travels only through the trusted local control channel and is never represented as
  a model tool call, transcript item, or audit event.

In normal agent mode, use Playwright refs for identity. Resolve the target's current bounding box,
choose a safe point inside it, and emit:

```json
{
  "type": "browser_action_visual",
  "version": 1,
  "action_id": "act_19",
  "tab_id": "tab_7",
  "snapshot_id": "snap_42",
  "frame_id": "frame_91",
  "sequence": 42,
  "phase": "move",
  "kind": "click",
  "target": {
    "ref": "f1e18",
    "x": 410,
    "y": 220,
    "box": {"x": 370, "y": 200, "width": 80, "height": 40}
  },
  "viewport": {"width": 1280, "height": 900, "dpr": 1}
}
```

The frontend renders a tilted triangular SVG “ghost cursor” above the browser frame, interpolates
from its last point to the target, then briefly shows a pressed state and click ripple. The
backend executes `locator.click()` against the same resolved ref; the animation never determines
the actual target. The same action emits `move`, `down`, `up`, and `completed`, `failed`, or
`cancelled` phases. Sequence and frame IDs let a delayed renderer discard obsolete animations.
The browser runtime does not wait indefinitely for presentation acknowledgement: a hidden or
disconnected panel must not stop execution.

This matches the observable Codex behavior without depending on private animation details:
Codex's public [Browser documentation](https://learn.chatgpt.com/docs/browser?surface=app#app-computer-use-in-the-browser)
promises opening, clicking, typing, screenshots, and verification, and the current Browser tool
surface exposes coordinate move/click as well as DOM/locator clicks. An
[official Codex Lab demo](https://webinar.openai.com/on-demand/f4d5175f-233a-44f8-af6d-a7170dcf484c)
calls the visible agent pointer a “ghost cursor.” The ghost pointer does not appear in captured
page screenshots, which is consistent with it being browser-shell presentation rather than page
content. OpenAI's public
[Computer Use guide](https://developers.openai.com/api/docs/guides/tools-computer-use) defines
coordinate move, click, double-click, drag, scroll, type, keypress, wait, and screenshot actions,
and its [sample app](https://github.com/openai/openai-cua-sample-app) executes them serially before
capturing the updated screenshot. OpenAI does not publicly document Codex's exact cursor renderer
or timing constants.

Cursor requirements:

- show the blue agent pointer only for agent actions; the user retains the normal system cursor
  during takeover;
- keep one pointer position per tab;
- use Playwright actionability checks, scroll into view, and recompute the target box immediately
  before action execution;
- hide or cancel the cursor on navigation, stale refs, tab changes, and viewport resize;
- render movement over roughly 180–320 ms and the click state over roughly 100–160 ms, tuned
  during UI testing rather than treated as a protocol guarantee;
- honor `prefers-reduced-motion` by jumping to the target and showing only a short static pulse;
- keep the overlay `pointer-events: none` and out of the accessibility tree.

Click presentation and execution order:

1. Revalidate the ref, run Playwright's trial/actionability checks, and scroll it into view.
2. Emit the post-scroll frame and viewport metadata.
3. Read the current locator box and emit the frame-bound `move` event.
4. If the primary panel is visible, wait no more than 500 ms for its matching
   `browser_cursor_arrived` acknowledgement. Hidden, disconnected, and reduced-motion views do
   not delay automation.
5. Revalidate, emit `down`, and call `locator.click()` at the corresponding relative point.
6. Emit `up` and `completed` only after Playwright succeeds; otherwise emit `failed`. Never fall
   back to the stale raw coordinate.

Playwright boxes use main-frame viewport CSS pixels, including iframe targets. Scroll first and
then measure; do not subtract document scroll offsets. Normalize the hotspot by the frame's CSS
viewport dimensions and render it inside the exact aspect-fit content rectangle, excluding any
letterboxing. Browser zoom, resize, navigation, or a new frame invalidates the old target and its
acknowledgement.

Store the complete accessibility snapshot server-side. Return no more than approximately 32 KiB
at a time, split at YAML node boundaries, with an opaque continuation cursor. Also support a
snapshot scoped around an existing ref. Any page-changing action invalidates continuations.

Do not globally truncate by tree depth: a measured 30-level fixture lost its actionable control
under a depth cap.

For the visible browser panel:

- send a frame immediately after each agent action;
- in passive follow-along mode, update at up to 2 fps while changing;
- during direct user interaction, use `Page.startScreencast` at up to 30 fps and acknowledge every frame;
- become event-driven while idle;
- use JPEG/WebP binary frames at CSS scale;
- never use base64 data URLs or place frame streams in model context.

Measured cost at 1280×720:

| Operation | Result |
|---|---:|
| AI snapshot, 80 cards | 6.4 ms p50 / 13.8 KB |
| AI snapshot, 500 cards | 26.0 ms p50 / 88.5 KB |
| AI snapshot, 2,000 cards | 86.0 ms p50 / 359.9 KB |
| JPEG 75 frame | 34.3 ms p50 / 79 KB |
| 2 fps frame stream | approximately 3.8% of one CPU core |
| 4 fps frame stream | approximately 7.9% of one CPU core |
| CDP screencast of an animated page | 60 fps / 16.6 ms p50 interval |
| CDP screencast frame in that fixture | approximately 8.6 KB JPEG |

An on-demand screenshot may be returned to a vision-capable model for visual verification. Raw
coordinate actions remain out of scope, so visual-only controls are handed back to the user.

The CDP viewport spike also verified that frame metadata reports CSS viewport dimensions and
scroll offsets, and that dispatching pointer input at the corresponding CSS coordinates triggered
the intended element at a 2× device scale.

### 6. Model-facing browser tools

Expose small, flat tools with primitive JSON-schema fields:

- `browser_open_url`
- `browser_history` (`back`, `forward`, `reload`)
- `browser_snapshot`
- `browser_snapshot_scope`
- `browser_snapshot_more`
- `browser_screenshot`
- `browser_click`
- `browser_fill`
- `browser_press`
- `browser_select`
- `browser_hover`
- `browser_scroll`
- `browser_tabs`
- `browser_select_tab`
- `browser_close_tab`
- `browser_dialog`
- `browser_console`
- `browser_close`

Defer drag/drop, upload, download, arbitrary JavaScript, raw selectors, network interception,
raw coordinates, and CDP.

OpenWorker's provider adapters do not preserve strict-schema behavior consistently. Validate
every tool call locally before policy evaluation or execution. Do not rely on a provider's
strict mode. Avoid `oneOf`, `$ref`, `const`, and polymorphic action schemas.

### 7. Network boundary

All browser egress goes through a per-context, authenticated loopback proxy with no direct
fallback. Playwright request routing is useful for observation, but is not the security
boundary.

The proxy must:

- canonicalize and resolve every destination itself;
- reject loopback, private, link-local, multicast, unspecified, non-global, and metadata
  addresses unless the exact local origin was explicitly granted;
- always reject cloud metadata addresses;
- pin the validated address for the connection to prevent DNS rebinding;
- repeat checks for redirects, popups, subresources, iframes, fetch/XHR, beacons, HTTP,
  HTTPS `CONNECT`, `ws`, and `wss`;
- fail closed on DNS failure, timeout, malformed proxy traffic, or proxy death.

Launch Chromium with loopback proxy bypass disabled, QUIC disabled, and WebRTC restricted from
non-proxied UDP. For an approved local development origin, grant exactly scheme, host, and port;
do not implicitly grant adjacent ports, aliases, LAN hosts, or metadata.

Block service workers in the MVP for deterministic routing and observation. Enabling them later
does not change the security design because the egress proxy remains authoritative, but requires
a compatibility suite first.

### 8. Permissions and consequential actions

Site permission and action confirmation are separate boundaries. The persisted site setting is
an exact normalized hostname, not a path, query, wildcard, or blanket approval for a
consequential action. In `ask` mode a new hostname prompts once and the decision carries across
conversations. `auto` admits low-risk public destinations after destination-policy validation;
`allow` skips public-host prompts. Explicitly blocked hostnames always fail. Private/local hosts
still require an exact saved decision in every mode.

The hostname boundary applies to agent-controlled top-level navigation. `browser_open_url` is
checked before approval and execution; ref-scoped anchors and form actions are resolved from the
live DOM and checked before their action. A BrowserContext main-frame route repeats the
non-interactive check for redirects, popups, history, and navigation that occurs during a click
or form submission, so a new `ask`-mode hostname fails closed instead of escaping the approval.
Subresources and child frames are intentionally not hostname-allow-listed because normal sites
depend on cross-origin APIs, images, fonts, and CDNs; every one remains constrained by the
public/private fail-closed destination proxy. Direct navigation while the user has taken control
bypasses the agent hostname check but not the network destination policy.

Page content is untrusted and cannot grant permissions, change the user's task, add filesystem
roots, enable tools, or provide credentials.

Run a mandatory `BrowserActionPolicy` after the ordinary permission engine. “Full Access” must
not bypass it.

Always confirm:

- form submission or Enter inside a form;
- send, publish, post, purchase, book, transfer, subscribe, or accept terms;
- delete, cancel, account, security, permission, or privacy changes;
- authentication and OAuth consent;
- disclosure of secrets, personal data, connector data, or local-file content;
- an ambiguous control such as “Continue” when its consequence cannot be proven safe.

Bind an approval to the exact origin, tab, snapshot, ref, action, parameter hash, and disclosed
data classification. It is single-use and short-lived. Revalidate the element and origin
immediately before execution. Never replay a consequential action automatically after a crash
or timeout.

### 9. Privacy and persistence

- Omit page text, screenshots, form values, cookies, headers, query strings, and typed values
  from JSONL, SQLite audit rows, logs, telemetry, and durable events.
- Do not expose input values in snapshots; expose role, label, type, and empty/non-empty state.
- Keep screenshots in memory over the authenticated, conversation-bound local channel.
- After a turn, replace raw browser tool observations with
  `[browser observation omitted after turn]`.
- Persist only origin, title, action status, timestamp, and redacted errors.
- Show a first-use disclosure that authenticated page content can be sent to the selected model
  provider.

### 10. Shutdown, crashes, and cleanup

- Task deletion cancels active browser work, closes that context, invalidates refs, and deletes
  ephemeral frames and task-linked metadata.
- Idle eviction atomically checkpoints a leased auth vault, then closes the context.
- App shutdown stops accepting actions, cancels and awaits calls, checkpoints profiles, closes
  contexts before the browser, stops Playwright, and then closes storage.
- A browser crash invalidates every ref. Restart only on a subsequent safe operation and never
  replay a write.
- The browser worker exits on control-channel EOF or failed heartbeat.
- On POSIX, own the browser descendants in a process group. On Windows, use a Job Object with
  `KILL_ON_JOB_CLOSE`.
- Startup may delete abandoned ephemeral directories, but never saved auth vaults.

## Compatibility boundary

The MVP supports normal accessible web interfaces, open Shadow DOM, iframes, popups, and
virtualized UIs through fresh snapshots.

The following require direct user interaction or are unsupported:

- CAPTCHA and anti-bot challenges;
- 2FA and hardware-backed sign-in;
- closed Shadow DOM and canvas-only controls;
- downloads, uploads, extensions, camera, microphone, location, clipboard, and notifications;
- sites that require service workers;
- sites that reject automated Chromium.

No stealth or challenge-bypass behavior is planned.

## Build readiness

Implementation can begin. No unanswered research question blocks the runtime, ref protocol, shared
viewport, or agent-pointer foundation.

Proceed with these MVP product defaults:

- one saved browser profile;
- “Remember sign-ins” is opt-in;
- always-on shared input with no ownership toggle or browser mutex;
- the tilted blue ghost cursor appears only for agent actions;
- attended-only operation with mandatory consequential-action confirmation, even under Full
  Access;
- service-worker-dependent and visual-only controls are unsupported and handed to the user.

The auth-vault library, proxy component, persistence scrubber, and exact cursor animation constants
are bounded implementation choices with stated contracts and tests. They do not require more
product research before coding their layers.

## Certification gates before release

Architecture and core feasibility are resolved. These platform checks remain:

1. Confirm Tauri's macOS custom-file staging preserves Chromium framework symlinks; otherwise
   inject Chromium after Tauri bundling and re-sign the outer app.
2. Run the real Developer ID signing/notarization job, inspect the notary log, and launch the
   installed DMG on a clean Mac.
3. Authenticode-sign the Windows payload and installers, then launch Browser Use on a clean
   Windows VM under Smart App Control.
4. Run provider conformance tests with OpenWorker's supported configured models; local schema
   validation remains authoritative.
5. Profile memory inside the packaged app because summed Chromium-process RSS double-counts
   shared pages.

These are bounded release tests. None requires changing the product architecture above.

## Required regression suites

The implementation is not complete without:

- ref identity, staleness, iframe, popup, Shadow DOM, and virtualization tests;
- private/reserved IPv4 and IPv6, mixed DNS, rebinding, redirects, WebSocket, WebRTC, QUIC,
  metadata, and killed-proxy tests;
- prompt-injection, ambiguous-action, element-replacement, origin-change, crash, and no-replay
  tests;
- cross-conversation isolation, profile lease, auth restore, corrupted vault, deletion,
  shutdown, and orphan-process tests;
- assertions that no page content, screenshot, typed value, cookie, token, or header reaches
  durable history, audit storage, logs, or telemetry.
