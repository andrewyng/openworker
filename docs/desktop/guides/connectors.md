# Connect your apps and tools

Let a coworker work with the services you choose, using your accounts and permissions.

Open **Settings → Connectors** to browse available integrations. A connector supplies tools for a service; connecting an account and enabling it for a conversation are separate choices.

## Connect an account

Choose the service and follow its setup form. Some integrations offer a browser sign-in; others accept an API key or personal access token. Review the permissions requested by the service before granting access.

Signing into OpenWorker can make supported account connections easier, but it is not required for local-file work or all manually configured integrations. The available setup method depends on the service.

## Enable it for the task

In the conversation's **Access** section, check the services enabled for that session. Turning a service off there mutes it for the session; it does not disconnect the account globally.

Start with a read-oriented task:

```text
Review this week's activity in the GitHub repository I specify and
draft a short progress update here. Don't post a comment or message.
```

Review results before authorizing outward actions such as sending a message, changing an issue or creating a calendar event. Approval mode controls how proposed actions are reviewed; the connected account's own permissions still apply.

## Add tools through MCP

The Connectors settings also support custom MCP servers. Add a remote URL or the supported server JSON, test the connection and review the tools it exposes. Enable only the tools needed for your workflow.

A local MCP server command runs code on its host. Only configure servers from sources you trust, and check where credentials are stored. Tool descriptions and results from a third party are context for the agent, not permission to act outside your request.

## On another machine

Connections are machine-scoped. Check the selected machine at the top of Settings. Some connection types can be enabled or moved from another machine; others require a separate sign-in or are not transferable. Use only the actions offered by that connection's card.

Removing a local connection does not necessarily revoke a credential at the provider, or remove a copy already deployed to another machine. Review both the machine's connection list and the provider's account settings when revoking access.

See [Remote machines](machines.md) and [Privacy and stored data](privacy.md).
