# Optional turn trace viewer

This standalone browser component uses the management UI's timeline, usage and
model/tool summaries. It adds no dependency to the Rust harness or TypeScript SDK.
It is a local source package pending release review, not a published npm package.

```js
import {mountTurnTrace} from './src/index.js';
import './src/style.css';

const view = mountTurnTrace(document.querySelector('#trace'), {
  adapter, // timeline(query), summary(query); see src/index.d.ts
  sessionId, turnId,
  onChild(child) { openTrace(child.child_session_id, child.child_turn_id); },
});
// On route change: view.dispose(). To reload: view.refresh().
```

The browser adapter calls your application's authenticated API. Keep harness,
Langfuse and database credentials in the trusted backend. Check session ownership
on every request. Optional adapter methods add `children`, `subtreeSummary` and
`evaluations` panels. The viewer never starts an agent or calls an evaluator.

Each instance has independent DOM IDs, requests and disposal. Timeline pages load
100 records at a time, to a browser cap of 1,000. Summaries use the server's separate
bounded scan; missing counters and truncation remain visible. Evaluations show the
first 25 results; later pages remain available through the SDK query API.

Linked work means the root turn and authorized spawned initial turns (up to 25),
not every later message in those child sessions. Tokens count model attempts once;
cache/reasoning counts are subsets. Concurrent task durations are not wall time.

`node sync.mjs` copies the canonical modules into the management UI's static
assets. Run `node sync.mjs --check` to catch drift. These are generated copies;
edit this package's source and sync it instead of maintaining two viewers.

For a loopback preview of qualification data:

```sh
node examples/serve.mjs /absolute/path/to/qualification.json 59602
```

Browser qualification (using an existing Playwright installation):

```sh
node test/browser.mjs http://127.0.0.1:59602 /absolute/path/to/playwright/index.mjs /tmp/trace-screenshots
```

This checks keyboard tabs, exact child navigation, independent mounted instances,
HTML escaping, disposal, and the 1,000-record pagination limit.

## Session tracing

The optional session browser adds paged session search, expandable turn lists,
recorded hierarchy, a per-turn waterfall and a step inspector. A child link opens
that child's session in the same pane. The breadcrumb restores the parent view.
The browser does not merge child tool calls into a parent's turn.

```js
import {mountSessionTracing, serviceTracingAdapter} from './src/index.js';
import './src/tracing.css';

const viewer = mountSessionTracing(document.querySelector('#traces'), {
  adapter: serviceTracingAdapter(async (path, signal) => {
    const response = await fetch('/api' + path, {signal});
    if (!response.ok) throw Object.assign(new Error('Trace unavailable'), {
      status: response.status,
    });
    return response.json();
  }),
});
// On route change or logout: viewer.dispose().
```

Your backend must authorize every request. The management UI uses its existing
session cookie and read-only proxy. Service session turns are paged through the
existing authorized run directory and filtered by exact session ID. Search results
are deduplicated into sessions; counts describe only loaded records. Turn headings
use stable IDs rather than inventing chronological turn numbers for partial pages.

For an embedded/managed SDK application, provide `adapter.turns` with
`harness.observability.listTurns`, and the existing `timeline`, `summary` and
`children` methods. Supply `initialRun` when your application already knows its
session. Search is optional. Each mounted instance has independent state.

Content stays unavailable unless the trusted application supplies `stepDetails`
for an exact `(sessionId, turnId, traceId, spanId)`. That function must enforce
ownership and capture/redaction policy before returning `{input, output}`. The
viewer does not scrape raw journals or infer payloads by matching tool names.
Provider-private reasoning and rejected output must not be supplied. Errors in
this optional callback do not affect agent execution.

Limits: 25 run rows per search page, 100 service run rows per turn-directory page,
100 trace records per timeline page; 1,000 loaded search rows, turns and records
per turn. Empty filtered pages can still have a next cursor. Waterfall bars use
recorded timestamps, unknown durations remain unknown, and hierarchy depth is
bounded. A session summary is not synthesized by adding overlapping child work.
Evaluations and the combined team overview are outside this view.

Replay selected local OpenShell qualification metadata for review:

```sh
node examples/serve-session.mjs /absolute/path/to/openshell-traces.json 59605
node test/session-browser.mjs http://127.0.0.1:59605 /absolute/path/to/playwright/index.mjs /tmp/session-trace-screenshots
```

The replay server is loopback-only, GET-only, serves explicit assets and the selected
fixture, and is not a production API. Browser tests distinguish recorded data from
injected race/denial fixtures. The real API/browser qualification lives in
`harness/service-ui/test/observability-live.mjs`, launched by the Rust
`recording_queries` integration test with `OW_OBSERVABILITY_UI_BROWSER=1`.
