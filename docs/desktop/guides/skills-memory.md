# Skills and memory

Teach a reusable workflow and keep the preferences you want coworkers to remember.

Skills, saved memory and conversation history solve different problems. A skill describes **how to do a task**. Memory stores useful persistent preferences. A conversation records **what happened in that task**.

## Use a skill for repeatable instructions

Open **Settings → Skills** to inspect and manage the skills available on the selected machine. A skill can describe a workflow such as preparing a weekly report, reviewing a document or following a team's writing convention.

Use the available create/install controls, or ask the coworker to help draft a skill:

```text
Help me create a weekly-status skill. Use decisions, progress, risks
and next steps as the sections. Keep the report under 300 words and
never send it automatically. Show me the instructions before saving.
```

Review the instructions and supporting files before accepting the skill. Installing instructions from a third party can steer an agent with your granted access; use sources you trust.

## Keep permissions separate

A skill does not grant an account, a folder or permission to publish. A writing skill can describe how to send a report, but the actual send still follows the conversation's authorization, tool permissions and approval mode.

The app-building skill for Codex or Claude Code is a different workflow for developers using the [Harness SDK](https://openworker.com/docs/sdk/). It is not required to use a Desktop App skill.

## Review saved memory

Open **Settings → Memory** to see stored preferences and your persistent instructions. Where enabled, **Remember new things about me** allows useful lasting details from conversations to be saved. Inspect, edit or remove entries that are wrong or no longer wanted.

Turning new saves off does not necessarily stop existing memories from being used. Remove saved entries too if you don't want them available to future conversations. Existing open conversations can retain earlier context even after a saved entry is changed.

Avoid storing credentials or other secrets as memory. A preference can become part of future model context, including context sent to a cloud provider.

## On a remote machine

Check the machine selected in Settings. Skills and memory are machine-scoped; don't assume a local edit has synchronized everywhere. Remote management support varies by feature and build. If the settings page directs you to a conversation on that machine, use that route instead of editing the laptop's settings by mistake.
