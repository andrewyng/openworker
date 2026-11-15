# Troubleshooting

Start with the machine, model and permission setting involved in the failed action.

Avoid repeatedly sending a request when an earlier action may already have completed. Read the activity, check the resulting file or service state, and resolve uncertainty before retrying an action that changes something.

## Can't send or the model fails

- Open **Settings → Models & Keys** and test the provider. Check account access, model ID and any upstream error.
- Select a connected model in the composer.
- For a custom endpoint, test a short reply and a tool call; text-only compatibility isn't enough for agent tasks.
- For a remote conversation, check the providers on the remote machine.

An authentication failure is different from a rate limit or a model-response error. Don't paste a key into chat to diagnose it. See [Connect a model](models.md).

## Files can't be read or written

Check the conversation's **Access** section, the actual file path and read/write grant. A ZIP must be extracted before its contents behave like a normal folder.

On a remote machine, paths refer to that machine. In a sandbox, check the session's folder grants and toolchain configuration. If writing is blocked, check that you aren't in Discuss or Plan mode. See [Conversations and files](sessions-files.md).

## The agent is waiting

Look for an approval card, a question or an Inbox item. Auto-approve can still escalate to a human. A task waiting on a decision has not necessarily hung.

For a long command, inspect its reported status and output before starting another copy. For an offline machine, wait for the connection to return before assuming a tool failed or completed.

## A remote machine won't connect

Check the controller address and port shown by the app, the SSH tunnel or private-network route, and the machine service. An expired join command needs a fresh enrollment window, not reuse of the old token.

If the remote agent starts but cannot call a model, check its own key deployment and provider configuration. If a protocol error appears, update the older side to a compatible build. See [Remote machine setup](machines.md).

## A sandbox blocks work

Read the named prerequisite, folder or host in the error. Confirm that the runtime is running and start a new conversation after setup changes. Add narrowly scoped access for the task rather than disabling the sandbox as the first response.

OpenShell image pulls need network access and disk space. Native sandbox tools may need a shared toolchain or login that an ordinary Terminal already has. See [Sandboxing](sandbox.md).

## Voice input isn't ready

Check **Settings → Voice input**, device compatibility, microphone permission and the model's verification status. Use **Repair** for a failed model verification. See [Voice and transcription](voice.md).

## Update the app

Use **Settings → General → Check for updates** when available. Save work and review what will restart before installing. A current source feature may not yet exist in the installed build; don't use instructions for a button that isn't present without checking its availability.

## Report a problem

Include your operating system, app version, selected coworker, local or remote machine, model/provider, sandbox mode and the steps that reproduce the failure. Add the relevant error and what you expected to happen.

Use a small synthetic example when possible. Redact credentials, enrollment links, private prompts and document contents before attaching screenshots or logs. Report issues in the [OpenWorker repository](https://github.com/andrewyng/openworker/issues).
