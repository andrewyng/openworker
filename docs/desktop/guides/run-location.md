# Choose where work runs

Select the machine before starting a conversation, then use that machine's files and models.

After you [join a machine](machines.md), the new-conversation setup row offers a **Runs on** choice. With no joined machines, that choice may be absent: work runs on your computer.

## Start a remote conversation

1. Choose **New session** and the coworker you want.
2. Set **Runs on** to the joined machine and check that it is online.
3. Choose a model available on that machine.
4. For folder-based work, select or enter a folder on the remote machine.
5. Check the access and approval mode, then send your request.

The selected machine belongs to that conversation. Opening a new conversation elsewhere doesn't move an existing session or copy its files.

## Keep the three locations straight

| Item | Which machine matters? |
|---|---|
| Repository, input files and generated outputs | The machine running the conversation |
| API keys and connected accounts used by the agent | That machine's configured connections |
| Local model endpoint | Reachable from the agent's machine; `localhost` means that machine |
| Voice dictation | The Desktop App device where you record |
| Tool sandbox | Configured on the machine running those tools |

To use a local file remotely, transfer or clone it there through an authorized workflow first. Selecting a folder on the laptop doesn't automatically mount it into the remote session.

## Follow remote work

Remote sessions appear in the app with a machine indicator. Open one to read its activity and continue the conversation. The Inbox can surface questions and approval requests from connected machines.

When a machine is offline, previously viewed conversations may remain readable as cached history. A cached transcript isn't a live connection: you cannot assume a command finished, a file changed or an approval was delivered until the machine reconnects.

## Decide between local and remote

Use your computer for quick tasks involving local documents or tools already installed there. Use another machine for a persistent development environment, Linux-specific dependencies or work that should keep running independently of your laptop.

A remote machine is not automatically a restricted environment. Configure [sandboxing](sandbox.md), network access and credentials on it just as deliberately as on your computer.
