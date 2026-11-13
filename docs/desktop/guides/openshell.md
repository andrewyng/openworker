# NVIDIA OpenShell integration

Run supported agent commands and file tools inside an OpenShell-managed container sandbox.

OpenWorker keeps the agent loop, model connections and approvals outside the execution container. It sends supported tool work to a runner inside the sandbox, where filesystem and network policy apply.

## Prepare the machine

Use a supported Linux host or a Mac with a working Docker Desktop setup. The Desktop App's **Settings → Sandbox** page offers OpenShell where supported and checks the required components.

Follow the setup path supplied by your OpenWorker build. OpenShell gateway, CLI and policy formats must be compatible; don't mix releases based on an unrelated SDK deployment guide. The app's checks and the errors they report are more useful than guessing a version.

On macOS, the setup may require Docker Desktop host networking and a sufficiently recent Linux kernel in Docker's VM. Enabling host networking can require a Docker restart; wait for other container work to finish first.

## Set up OpenShell

1. Open **Settings → Sandbox** on the machine that will run the work.
2. Choose OpenShell and follow its prerequisite checks and **Set up** flow.
3. Review the setup changes and image download before continuing. Allow several gigabytes of free space for container images and sandbox storage.
4. Choose the folders and network access needed by the task.
5. Start a new conversation and verify the sandbox indicator.

## How files and tools work

OpenWorker makes the session's selected folders available to the tool runner with their intended read/write grants. Other host files are not automatically present. A package or server installed in an ordinary host shell is not necessarily installed inside the container.

Shell processes execute in a Linux environment. A macOS application command or Windows executable won't become a Linux program because it was available on the controller.

Network policy restricts the container's destinations. Check the host needed by a failed Git clone, package install or web request rather than granting unrestricted internet access as the first fix.

## Approvals and connectors still apply

OpenShell does not replace OpenWorker's [approval reviewer](approvals.md). The app decides whether a tool action may proceed; the execution boundary restricts what the permitted command can reach.

Service connectors run outside the tool sandbox. Their account permissions and approval rules remain important. Don't infer that a connector is blocked merely because a shell command cannot reach the same service.

## When setup fails

Check that the container runtime and gateway are running, the required networking is enabled and disk space is available. Follow the app's reported prerequisite failure before retrying. Avoid repeated container recreation when a host prerequisite is still missing.

This page covers the Desktop App integration. Deploying a Harness SDK service cluster is a separate [developer workflow](https://openworker.com/docs/sdk/openshell/).
