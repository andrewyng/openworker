# Messaging ↔ Sessions — design

**STATUS: design (draft for review).** Discussed with Rohit 2026-06-27. How a messaging
platform (Slack/Telegram) connects to persona *sessions*: ambient **channel subscription**
(inbound pub/sub) vs. the **Inbox** (targeted request/reply). Companion to `PERSONAS.md`
(sessions, self-wake, no "always-on") and `PERMISSIONS-AND-INBOX.md` (the Inbox, routing,
gateway). Decisions log at the bottom.

## Two mechanisms, opposite semantics

The core insight: connecting messaging to a session is **two different things**, and naming
them apart makes both simple.

| | **Channel subscription** | **Inbox** |
|---|---|---|
| Pattern | publish / subscribe (broadcast) | request / reply (point-to-point) |
| Direction | inbound, ambient | agent asks → user answers |
| Correlation | **none** — messages are just *visible* to subscribers | **critical** — answer must return to one session + one tool call |
| Fan-out | many sessions per channel (a feature) | exactly one waiter per item |
| Agent state | may arrive while working (a steer) or idle (a new turn) | the agent is *blocked* on the answer |

**The discriminator that lets both share one channel:** an inbound message carrying an
`[ocw:<id>]` token (or a threaded reply to a delivered Inbox message) is an **Inbox answer** —
it resolves that item and is **consumed** (never broadcast). Any other inbound message is a
**channel message** — fanned out to every subscribed session. This is the existing
`reply_resolver`-tried-before-handler pattern (`connectors/gateway.py`), generalized.

## Sessions are durable — they never "end" (decided with Rohit 2026-06-27)

A session is **not** tied to a socket, a process, or a turn. It lives in the conversation store and
is **always resumable** — a user message, a self-wake, an Inbox resolution, or a channel message can
wake any session at any time (busy → steer, idle → a fresh background turn; **no live socket
required**). So:

- **A session never fully dies.** The only thing that removes a session is the **user explicitly
  deleting it**. "The GUI is closed" / "the socket dropped" / "the process restarted" do not end a
  session — they just mean the next interaction resumes it from the persisted thread.
- This is why durable identifiers bind to **`session_id`**: it's a stable handle that always
  resolves to a resumable session. (It's also the deeper reason the parked-Inbox-prompt model works
  — see `PERMISSIONS-AND-INBOX.md`: a prompt belongs to the session, not the connection.)
- Practical consequence for subscriptions (below): a `(session_id, channel)` subscription is
  **permanent** until explicit teardown; it isn't "lost" when the session goes idle or the server
  restarts. Deleting the session is the one implicit unsubscribe.

## Channel subscription (inbound pub/sub) — **v1 built 2026-06-28**

**Subscription ≠ Inbox routing.** Routing is *outbound* (mirror an agent's approvals/questions OUT
to a DM/channel; request↔reply, `[ocw:id]`-correlated). A subscription is *inbound* (bring a
channel's messages IN; broadcast). Keep them on different channels — pointing your Inbox at a
channel you also subscribe to conflates the two directions (a guard warns when they collide).

- **Subscription** = a persisted `(session_id, channel)` record (`channel` = the address
  `"<platform>:<chat_id>"`). Many sessions may subscribe to one channel (two agents, two reactions).
  **Permanent until the user or the agent unsubscribes** (or the session is deleted). Survives
  restarts (see "Sessions are durable").
- **Created by the agent via a tool** (`subscribe_channel` / `unsubscribe_channel` /
  `list_subscriptions`), registered for messaging personas. The agent **bootstraps by asking the
  user which channel** with `ask_user` — it doesn't autonomously know channels. (Slack encodes a
  typed `#alerts` as `<#C0123|name>`, so `subscribe_channel` parses the id straight out of the
  answer; a raw address works too. A GUI channel picker is a later nicety.)
- **Delivery** of a (non-token) channel message → every subscribed session, via the self-wake path:
  **busy → `queue_steering`; idle → a fresh background turn.** Each agent decides from its **own
  role/prompt** whether the message is relevant — that judgment *is* the per-agent routing. A
  channel with **no** subscribers is buffered but delivered to no one; a **DM** with no subscription
  falls through to the default super-agent session.
- **Catch-up tool** `get_channel_messages(channel, n)` — last *n* messages from an in-memory ring
  buffer filled as messages arrive (so an agent can subscribe, then ask "what did I miss?").
- **Reply** via the existing `send_message(target, text)` tool, targeting the channel address
  carried in the delivered message.
- **Loop prevention is free:** the Slack adapter already drops the bot's own messages
  (`bot_id`/`subtype`/`user==bot_user_id`). With one bot identity, *every* agent's channel post is
  "the bot," so agent-↔-agent and self-loops are filtered at the source.

**v1 filter:** a subscribed channel delivers **all** its (non-bot, non-token) messages — the
subscription *is* the filter. "Mentions + their threads only" is a later refinement: the inbound
`MessageEvent` doesn't yet carry "was the bot @mentioned" (the bot's user id is known, so it's a
clean adapter add later).

### The Slack identity constraint (why "@mention per agent" doesn't work natively)

OpenCoworker connects as **one bot user** (one token = one Slack identity). A native `@mention`
resolves to that single bot — so you **cannot** natively `@mention` "Ops" vs "Research" as
separate Slack users without giving each persona its own Slack app install (heavy; kills
two-click subscribe). So "mention" operates at **two levels**:

1. **Bot-level mention = the activation filter (native, one identity).** A message that
   `@OpenCoworker`s the bot, or is a **DM**, or is in a **thread** the bot is in, is what marks it
   "for me" vs. channel chatter. This is the noise/cost filter — *not* per-agent routing.
2. **Subscription + the agent's role = the fine routing.** A bot-mention on a channel is
   delivered to **all** subscribed sessions; each judges relevance from its own prompt. No
   per-agent identity needed.
3. **Optional explicit targeting = a text convention, not a Slack mention.** e.g.
   `@OpenCoworker ops: …`, parsed from text against a subscription's declared keyword. Nice-to-have.

**Default filter:** deliver only bot-mentions / DMs / in-thread replies (so idle agents aren't
woken by every line on a busy channel). A subscription may opt into "all messages" for a
dedicated channel. **A DM with no channel context → the user's default persona session** (the old
super-agent role).

**Per-persona Slack identities** ("@OpsCoworker" as a distinct teammate) are nicer UX but a
**multi-install managed-connector story** — parked as a future/premium path, not v1.

## Mirroring the Inbox to a channel — **interactive buttons** (built 2026-06-28)

How does the user *answer* an Inbox prompt from Slack? Not by replying with `[ocw:id]` in the text
(Slack does nothing to keep the token in a reply — a bare "yes" loses it; that path is brittle).
Instead the mirrored item is a **Block Kit card with buttons**, and **the item id rides inside each
button's `value`** (`{"id":…, "r":"allow"}`). A click sends an interaction payload that names the
exact item + choice — unambiguous, no token, no thread tracking.

- **Discrete choices → buttons:** approvals (`Approve`/`Deny`), `ask_user` **options** (one button
  each). `interactions.buttons_for(item)` builds them; `encode`/`decode` own the value.
- **Free-text answers are NOT offered over messaging** (decided with Rohit): that prompt shows
  "*open the app to respond*" — the user types in the App. (The `[ocw:id]` token stays only as a
  legacy reply fallback for those.)
- **Inbound:** socket mode delivers the click over the same connection (no public endpoint — just
  "Interactivity" enabled in the Slack app). `SlackAdapter` `@app.action("ocw_*")` → gateway →
  `manager._on_interaction` → `inbox.resolve(id, r)` → the buttons are swapped for the outcome
  ("✅ Approved by @you"). Resolving releases any suspended agent (first-responder-wins with the app).
- **Provider-agnostic:** a `Button(label, value)` the adapter renders natively; Telegram inline
  keyboards + plan/directory buttons are follow-ups. v1 = single-select, Slack.

This largely **retires the brittle token-reply path** for the common case — buttons carry the id.

## Inbox correlation (targeted request/reply)

The Inbox item **id is the correlation key**; a button's value (or, legacy, the `[ocw:id]` token)
carries it.

**1. Live correlation — already works.** The asking session's engine is suspended mid-tool-loop
at `await store.wait(item.id)`. Resolving by id fires *exactly that await*, so "right session +
right tool call" is handled **structurally** — the answer returns to the precise point in the
precise engine. No extra bookkeeping while the process is alive.

**2. Durable correlation — the hard part (not yet built).** If the server restarts or the session
is evicted before the answer arrives, the in-memory `await` is **gone**. To survive that, the
Inbox item must persist enough to *reconstruct* the suspension:
- `session_id` + **`tool_call_id`** (+ tool name/args).
- On resume: rebuild the session engine and **inject the answer as the tool result for that
  `tool_call_id`**, then continue the turn (rather than re-calling the tool).

Today items hold `session_id` but **not** `tool_call_id`, and resolution only fires a live waiter.

**3. `ask_user` tool — the general Q&A primitive (new).** Approvals (allow/deny) already ride the
permission/approver path. Free-text Q&A ("which region?") needs a first-class tool the agent
calls that creates a `KIND_QUESTION` item, blocks, and returns the answer string — generalizing
the approver. It is the natural owner of the `(session_id, tool_call_id)` capture.

**4. Approvals have no tool-level id** — so the **Inbox item id is the generated correlation
handle** for them (we also capture the engine's tool_call where one exists).

## Relationship to what's already built

- `connectors/gateway.py` — `reply_resolver` (consumes `[ocw:<id>]` → `resolve_from_reply`),
  allowlist, inbound dispatch. Channel subscription extends this: non-token → fan out to subscribers.
- `inbox_routing.py` — named inbox = queue + binding (Slack/Telegram); `deliver` embeds `[ocw:<id>]`.
  Subscription is the **inbound** counterpart of the **outbound** binding.
- `inbox.py` — `InboxStore` + state machine + `wait`/`resolve`. Needs: `tool_call_id` on items;
  `ask_user` path; (later) durable resume.
- Self-wake busy→steer / idle→new-turn (`manager.resume_due_wakes`) — reused verbatim for inbound
  channel delivery.

## Decisions settled (2026-06-27)

- Two mechanisms: **channel subscription** (pub/sub, no correlation) vs. **Inbox** (request/reply,
  correlated); the `[ocw:<id>]` token discriminates inbound on a shared channel.
- Channel subscription = `(session_id, channel, filter?)`; many sessions per channel; delivery via
  busy→steer / idle→new-turn; `get_channel_messages` catch-up tool.
- **One bot identity.** Bot-mention/DM/thread = activation filter (default); subscription + agent
  role = routing; optional text-keyword = explicit targeting; **per-persona Slack identities =
  future path**. DM with no channel → default persona session.
- Inbox: item id = correlation key; **`ask_user` tool added** as the general Q&A primitive;
  approvals use the item id as their correlation handle.
- Durable resume: **best-effort live-only for v1** (a restart-orphaned question is re-surfaced for
  the agent to re-ask); **hardened replay-as-tool-result** (persist `tool_call_id`, inject as tool
  result on resume) is a Phase-2 follow-up.

## Decisions settled (2026-06-27/28 — channel subscription build)

- **Sessions are durable; they never end.** A session is always resumable (no socket/process
  dependency); **only explicit user deletion** removes one. Durable bindings use `session_id`.
- **Subscription is INBOUND; Inbox routing is OUTBOUND** — orthogonal directions; a guard warns when
  a subscription and a routing target collide on the same channel.
- **Subscription = persisted `(session_id, channel)`, permanent** until explicit unsubscribe (agent
  tool or user) or session deletion (the one implicit teardown). Survives restarts.
- **Agent-tool creation** (`subscribe_channel`/`unsubscribe_channel`/`list_subscriptions`); the
  agent **bootstraps via `ask_user`** to learn the channel from the user. Slack `#channel` mention
  tokens (`<#id|name>`) are parsed for the id; no name→id API lookup in v1.
- **v1 filter = the subscription itself** (deliver all of a subscribed channel's non-bot messages).
  Mention/thread filtering deferred (needs the adapter to surface `@bot`; the bot's user id is
  known, so it's a clean later add).
- **Loop prevention is already handled** by the adapter (drops bot-self messages); one bot identity
  makes agent-↔-agent loops impossible too.
- **`get_channel_messages`** = an in-memory ring buffer (last N/channel), not the Slack history API.
- Delivery reuses `manager.deliver_to_session` (busy→steer / idle→background turn), shared with
  self-wake. Reply via `send_message` to the channel address.
- **Answering an Inbox prompt from Slack = buttons, not free-text replies.** The item id rides in
  each button's value (Block Kit; socket-mode action callback → `inbox.resolve`), so correlation is
  unambiguous and the brittle `[ocw:id]`-in-reply path is mostly retired. **Free text isn't offered
  over messaging — the user opens the app** (token kept only as a legacy fallback). v1 = single
  select, approvals + ask_user options, Slack; Telegram inline keyboards + plan/directory = later.

## Open questions / follow-ups

- ✅ Subscription persistence — done (`subscriptions.json`, `SubscriptionStore`). **GUI** to view /
  manage subscriptions per session is still to build (v1 is agent-tool-driven).
- Authorization: subscription is a per-session opt-in on top of the gateway allowlist — confirm the
  trust model (who can subscribe a session to which channels). v1 lets the agent self-subscribe.
- Mention/thread filtering: surface `@bot` in the inbound `MessageEvent` (Slack adapter has
  `bot_user_id`), then let a subscription opt into "mentions only."
- Telegram analog of "@mention / thread" (groups vs. DMs) — map the same filter concept.
- Channel name→id resolution / a channel picker (so the user can say "#alerts" in the GUI, not only
  in Slack where the id is encoded).
