# Schedule recurring work

Turn a task you understand into work OpenWorker repeats on a schedule.

Open **Automations** in the sidebar. Start with a template or **New automation**, or ask a coworker to propose a recurring task in a conversation. Review the instructions, connections, destination and schedule before saving it.

## Start with a draft-only workflow

Try a recurring brief that saves a result in the app before adding outward actions:

```text
Every weekday morning, prepare a short summary of the project updates
available through the connections I enable. Keep the result in the
app for me to review. Don't post or send it anywhere.
```

Confirm the schedule and the actual sources. A task that posts to Slack or updates another service has a different permission scope from one that only prepares a draft.

## Review a run

Each run has a conversation you can open from the automation. Check what it read, what it produced and whether it is waiting for a question or approval. Use **Run now** for an initial test and verify the output before relying on the schedule.

Where a template offers a standing permission for a specific recurring action, read and approve that scope deliberately. Don't assume one manual approval authorizes all future runs.

## Keep the host available

Scheduled work requires the OpenWorker process on the machine hosting it to be running. A sleeping laptop is not an always-on worker. The app offers a **Keep this system awake** preference, and a [joined remote machine](machines.md) can provide a more persistent host when the task is created there.

Check the schedule displayed by the app, especially around travel or time-zone changes. A missed schedule does not imply every missed occurrence will be replayed; inspect the next run and history.

## Pause and change it

Pause an automation when its inputs, permissions or intended audience change. Review its saved instructions and delivery target before enabling it again. A successful run last week doesn't prove a changed external service or credential still works today.

For unattended decisions, review [Approvals and Auto-approve](approvals.md#when-you-are-away).
