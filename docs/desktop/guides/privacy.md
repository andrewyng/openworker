# Privacy and stored data

Understand where your work lives and which services receive it.

OpenWorker is local-first, but a workflow can still use cloud models, connected accounts, remote machines or third-party tool servers. The choices you make determine those data paths.

## Where information goes

| Information | Location or destination |
|---|---|
| Local conversations and working files | Stored on the computer running those sessions |
| Model context | Sent to the selected model provider or model server, including relevant messages, attachments and tool results |
| Connector requests | Sent to the connected service using its configured account |
| Dictation audio | Processed locally by the native transcription engine; the recording is held in memory for the operation |
| Transcribed text after you send it | Part of the conversation and its model context |
| Remote conversations and outputs | Stored on the joined machine; the Desktop App may keep a read-only cache of viewed history |
| Optional external recordings or telemetry | Sent to an export destination only when configured; review its content-capture settings |

Using a local model does not turn off connectors or shell networking. Using local transcription does not stop the resulting message from going to a cloud model when you send it.

## Credentials and account connections

Provider and connector credentials are managed through the app's local configuration and secret storage. Keep them out of prompts, screenshots and reports. Supported browser sign-ins may use OpenWorker's cloud service to broker an authorization flow; manual credential setup is available for supported integrations.

Deploying a key to a remote machine gives that machine its own copy. Encryption in transit to the machine does not mean the machine cannot use the credential after receiving it. Rotate or revoke it at the provider if the machine is no longer trusted.

Removing a connection from one machine need not revoke it at the provider or delete copies held elsewhere. Review the provider's own access controls too.

## Share files deliberately

Give access to a task-specific folder. A shared file can be read into model context, even when you marked it read-only. Read-only controls modification; it is not a confidentiality setting.

Sandboxes limit supported tool execution. The model loop and connectors operate outside them, and explicitly shared command-line credentials may be available inside. See [Sandboxing](sandbox.md).

## Memory, recordings and exports

Review saved preferences in **Settings → Memory** and remove details you don't want reused. Existing open conversations can still contain earlier context after a saved memory is changed.

If your build exposes observability or recording export, review the selected machine, destination and included fields before enabling it. Messages, tool inputs/outputs and names can contain sensitive content. Exporting a recording creates another copy governed by the destination's retention and access policies.

## Before sharing diagnostics

Remove API keys, join links, private document contents, account identifiers and sensitive paths. Logs and transcripts can include tool arguments, provider error details and file excerpts. A masked credential field does not imply the rest of an exported conversation has been redacted.

For a useful bug report, see [Troubleshooting](troubleshooting.md#report-a-problem).
