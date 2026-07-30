---
name: browser-use
description: Driving a real browser well — find elements via the accessibility tree, click by coordinates, handle SPAs, iframes, dialogs, downloads, and login walls. Read this before non-trivial browser work.
---

# Driving the browser

Your browser tool is `browser_exec`: it runs Python against a real Chrome through the
Browser Use CLI, with the CLI's helpers already in scope. There are no other browser
tools — every interaction is code through this one.

Adapted from Browser Use's own skill. The upstream version invokes `browser-use` from a
shell; here the same helpers are reached through `browser_exec`.

## When not to use the browser

A plain fetch of public information needs no browser. If `web_fetch` or `web_search` can
read it — a public page, an API, docs — use those. Reach for the browser when the task
needs interaction (click, type, navigate), the user's logged-in session, JS rendering, or
a page that blocks plain fetches. If a fetch returns a shell page, then escalate.

## Finding and clicking things

Prefer the accessibility tree over screenshots. Every element's role, name and
`backendDOMNodeId` is in it, and it is far smaller than the DOM:

```python
# Find the search box on the results page
nodes = cdp("Accessibility.getFullAXTree")["nodes"]
hits = [n for n in nodes if n.get("role", {}).get("value") == "textbox"]
print([n.get("name", {}).get("value") for n in hits][:20])
```

Filter in Python before printing — the full tree is thousands of nodes.

Node → coordinates → click:

```python
q = cdp("DOM.getBoxModel", backendNodeId=nid)["model"]["content"]
x, y = sum(q[0::2]) / 4, sum(q[1::2]) / 4   # viewport px
click_at_xy(x, y)
```

Negative or oversized coordinates mean the element is off-screen — scroll first. After any
click that navigates, call `wait_for_load()`, then verify with a targeted `js(...)` or
`page_info()` check rather than assuming it worked.

Fall back to raw HTML through `js(...)` when the AX tree lacks the element (canvas, exotic
widgets). Take a screenshot when layout or imagery is what actually matters.

## Pages that render late

`wait_for_load()` misses single-page apps: the document is "complete" before the framework
paints. After route changes and data fetches use `wait_for_element(selector, timeout=10)`,
or `wait_for_network_idle()`. If the current tab is stale or internal, call
`ensure_real_tab()`.

## Typing

`fill_input(selector, text)` focuses, clears, types with real key events, then fires the
`input`/`change` events frameworks listen for. Plain `type_text` bypasses those listeners
and can leave a submit button disabled, so prefer `fill_input` on React/Vue/Ember forms.

## Batching and the persistent session

`browser_exec` calls run in one persistent Python session: variables you assign survive to
the next call, so parse once, keep the result, and build on it. Batching a whole
sub-procedure — navigate, wait, extract, print — into one call is much faster than one
call per action, and the printed output is what you get back. Print the path of any
screenshot you save and it comes back as an image you can see. If a call times out the
session restarts (the browser survives); re-derive what you need from the page. Start the
code with a one-line `#` comment describing the step; it becomes the label the user sees.

## When a click does nothing

Before clicking, check the target is real: `el.disabled`, and
`document.elementFromPoint(x, y)` at your click point — if it returns an overlay instead
of your element, the click will land on the overlay. After clicking, verify something
changed (`page_info()`, a targeted `js(...)` check) instead of assuming. Never retry an
identical click that changed nothing — dismiss the overlay, pick another element, or use
`fill_input`/`press_key` for form controls.

## Login walls

Stop and ask. The exception is SSO where Chrome is already signed in — use it. Always stop
for passwords, MFA, consent screens, or an ambiguous account choice.

## Cloud browsers

A cloud browser is a fresh, isolated Chrome hosted by Browser Use, with clean managed IPs.
Prefer one when the work is bot-sensitive (scraping, repeated automated visits), when the
user's own browser and IP should stay out of it, or when parallel tasks would otherwise
fight over one local Chrome.

Cloud browsers are currently disabled: every session runs against the user's local Chrome.
If a task would clearly be better on a clean isolated browser, say so and let the user
enable it (`browser_backend = "cloud"` in config) rather than trying to arrange one.

Do not start or stop remote daemons by hand inside `browser_exec` — a daemon started that
way is not the one the other tools talk to, so the work would land somewhere invisible.

## Escape hatches

Raw CDP is always available with `cdp("Domain.method", **params)`. Downloads,
cross-origin iframes, drag-and-drop and dialogs each have their own mechanics; if you get
stuck on one, say what you tried rather than retrying the same call.
