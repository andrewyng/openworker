# Native sandboxing on Mac and Windows

Use the operating system's protections to restrict supported agent tools without a container runtime.

Choose the machine in Settings, then open **Sandbox**. The native provider available there depends on its operating system. Setup and network settings are machine-specific.

## On macOS

Choose **macOS sandbox** after enabling sandboxing. OpenWorker runs supported command and file tools under a macOS Seatbelt profile derived from the session's folders and network settings.

The profile allows the selected read-only and read-write folders, required system resources and private temporary storage. It does not grant general access to your home folder. Commands started by the runner inherit the restrictions.

Developer tools under your home directory may need to be enabled in the sandbox's toolchain settings. Network access can use an allowlist proxy; check the chosen hosts before trying package installs or Git commands. Some system/process tools are restricted even when they work in an ordinary Terminal.

## On Windows

Choose **Windows sandbox** and follow **Set up** if prompted. Its one-time setup needs administrator rights. It creates restricted local accounts and configures the filesystem and network controls used by the runner.

Commands run under a sandbox account, rather than as your normal desktop user. Session folders are explicitly granted, and the account has its own temporary area. This feature is OpenWorker's native Windows execution sandbox; it is not the Microsoft Windows Sandbox desktop application.

Review the network mode carefully. **Allow everything** permits network access while keeping the filesystem boundary. **Only the sites you allow** uses the restricted network path and proxy. Don't assume that turning the sandbox on also selected a closed network policy.

If you aren't an administrator, use the setup instructions to involve one. Don't replace a failed sandbox setup with broad permissions on your user profile.

## Share logins deliberately

In **Config & keys exposed to agents**, supported entries can make a command-line login or configuration available inside the sandbox. Examples include SSH, GitHub CLI, cloud CLI configuration or package-registry settings.

Review the source path, whether it contains credentials and which hosts the tool needs. A credential can authorize consequential actions even when the file itself is copied read-only. OpenWorker's supported sharing mechanism copies selected entries into the session's private environment; it does not mean every login in your normal account is inherited.

A browser login, OS credential store or SSH agent is not necessarily shareable as a file. A GitHub connector is a separate option: its credentials stay with the app's connector execution rather than being handed to `git` inside the sandbox.

## Check and troubleshoot

Start a new session after changing the provider or profile. If a command can't see a toolchain, check its configured path. If a host is denied, review the named destination and add only the access the task needs.

Adding folders to an existing session can require the tool environment to restart, which can lose shell state. Don't treat a background server as durable merely because the conversation remains saved.

For an alternative execution boundary, see [NVIDIA OpenShell](openshell.md).
