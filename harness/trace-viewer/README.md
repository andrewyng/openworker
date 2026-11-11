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
