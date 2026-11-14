# Persistent personal bots

Open **Bots** in the sidebar to create a named assistant with standing instructions.
Each bot owns a durable conversation, workspace and FIFO delegation queue. Closing
the conversation does not stop the host from processing its queue. Files,
conversation history and workspace memory remain available on later turns.
Global memory remains shared according to the existing Memory settings.

## Delegate and follow up

1. Create a bot, giving it a name, role and goals.
2. Add a task to its queue. The host checks queued work on its scheduler tick
   (normally within 30 seconds).
3. Open the conversation to see results, live tool activity and follow up.
4. Review task history. **Turn finished** means the runtime completed that turn,
   not that the real-world goal has been independently verified.

The same conversation is reused for every delegation and check-in. Busy
conversations defer queued work rather than mixing routine prompts into a live
turn. Existing channel subscriptions and self-wake tools can target this
conversation, subject to the existing connector permissions.

## Proactive check-ins

Optionally set a five-field cron schedule when creating the bot. For example,
`0 9 * * 1-5` checks in at 09:00 on weekdays in the displayed IANA timezone.
Check-ins ask the bot to advance existing authorized goals and report meaningful
changes or blockers. They do not expand its permissions. Missed schedules produce
one catch-up check-in, not a burst of all missed occurrences.

## Approvals, pause and restart

Bots start in Interactive permission mode. Background questions and approvals go
to Inbox; existing session controls govern tools, connections and sandboxing.
No blanket approval is added by creating a bot.

**Pause bot** interrupts current work and blocks background delivery, check-ins
and queued delegations. Already completed external effects cannot be undone.
Queued tasks stay on disk and resume when the bot is enabled again. Pending
self-wake timers are cancelled by Pause; use the configured check-in or delegate
a task to restart work after resuming.

An interrupted in-flight delegation is marked **Interrupted** after host restart.
It is never silently replayed: inspect the conversation before submitting a new
task, since the previous turn may already have performed an external action.

## Run each bot in its own cloud environment

Deploy a dedicated runtime on your cloud host using the
[per-bot deployment guide](../../../deploy/bots/README.md). Each deployment has
its own persistent disk, process, machine identity, provider keys and headless
browser profile. Use separate VMs if you need a separate kernel for every bot;
containers on one cloud host share that host's kernel.

Once enrolled through **Settings → Machines**, an unused dedicated runtime
appears in **New bot → Where this bot runs**. The remote runtime itself enforces
one bot and refuses unrelated conversation engines. Shared machines are not
offered as dedicated destinations. Configure models and integrations on that
runtime; the desktop does not automatically copy credentials.

The Bots page combines local and connected remote bots and routes each action
to its owning runtime. Offline destinations never fall back to this computer.
Previously discovered offline bots remain visible for this app session;
initial discovery requires connectivity. Close the desktop and the bot continues
on its cloud host, with questions/approvals parked until you reconnect.

Local bots still share this host and global memory according to the existing
settings. Creating a local bot only gives it another workspace, not another
computer. The deployment generator writes configuration; it does not purchase
or create a cloud VM. Email identities and third-party accounts must still be
configured through the appropriate services.
