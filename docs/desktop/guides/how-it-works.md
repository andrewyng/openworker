# How OpenWorker works

Choose a coworker, give it context and work together in a conversation.

Four concepts help explain the choices you see in the app. You don't need a remote machine or a sandbox to try your [first task](first-task.md).

## Coworkers have different jobs

A **coworker** is an agent configured for a role, with instructions, tools and skills suited to that work. The default OpenWorker coworker handles everyday tasks. Code focuses on a repository. Security coworkers focus on particular kinds of investigation.

Choose the coworker when starting a conversation. Enabling a coworker makes it available in the picker; it does not give it every connected account or folder automatically. See [Choose a coworker](coworkers.md).

## A conversation is the ongoing task

A **conversation**, also called a session, holds your messages, the coworker's responses and its tool activity. A **turn** starts when you send a message and can contain several model calls and tool calls before the coworker responds or waits for you.

Continue in the same conversation to refine earlier work. Start a new one for an unrelated task or a different coworker or machine. A saved conversation gives you continuity; it doesn't mean every command that was running survives a computer restart.

## A machine is where work runs

By default, work runs on your computer. You can [add another machine](machines.md), such as a Linux VM, and choose it before starting a conversation.

The remote machine runs the agent and owns its files, conversation data and credentials. A folder on your laptop is not automatically a folder on that machine. The Desktop App lets you interact with the remote session and respond to its questions and approvals.

```text
                 Your Desktop App
                 messages · approvals · results
                        |
             +----------+-----------+
             |                      |
        This computer          Joined machine
        local sessions         remote sessions
        local files            that machine's files
             |                      |
        Optional sandbox       Optional sandbox
        for supported tools    configured there
```

## A sandbox limits tool execution

A **sandbox** restricts supported commands and file tools using an operating-system or container boundary. It is separate from the machine choice: a remote VM is a machine, and that machine can also run sandboxed tools.

The agent loop, approvals and connectors run outside the tool sandbox. An action through a connected account is governed by that account's permissions and OpenWorker's approval rules, not by the sandbox's filesystem boundary.

Read [Sandboxing](sandbox.md) before enabling it, especially if the task needs network access or command-line logins.

## Permissions apply to actions

Folder access determines which files you share. Connected-account permissions determine what those tools can do. Approval mode determines how proposed actions are reviewed. Sandboxing adds an execution boundary.

These controls work together; none is a substitute for the others. Start with a small folder and [Ask for approval](approvals.md), then expand access deliberately as your task needs it.
