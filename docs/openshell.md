# OpenShell — run an agent's commands inside a sandbox

OpenWorker can run a session's shell commands and file tools inside an
[NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) sandbox: one Linux container per
agent that holds the session's folders and nothing else of your machine. The agent loop,
your model keys and every connector stay outside it. This works on Linux and on a Mac
(with Docker Desktop, where the sandboxes run inside Docker's Linux VM against your
folders).

A few things are true by design, before any setup:

- **Files: only the session's folders.** A sandbox sees the folders the session was given,
  at the same paths, read-write or read-only as the session has them. The rest of your
  home folder — `.ssh`, `.aws`, browser profiles, documents, OpenWorker's own state and
  its API token — is not there.
- **Network: only an allow list.** Package registries and code hosts by default; every
  other host is refused.
- **Secrets are absent, not denied.** Nothing in the sandbox holds a key. A prompt
  injection that lands in a sandboxed command lands in a box that holds nothing.
- **The wall is enforced by the container, not by the model.** Every process a command
  starts inherits it, and a session is refused rather than run open when OpenShell
  cannot be used.
- **Every tool result says which mode produced it.** The enforcement level is recorded
  on each tool-call event and in the audit trail.
- **Nothing changes until you choose.** The desktop app runs commands directly, as it
  always has, until you turn OpenShell on for the machine.

## How it fits together

```text
 your machine
 ┌─────────────────────────────────────────┐      ┌──────────────────────────────────────────┐
 │ OpenWorker (desktop app or local server)│      │ OpenShell sandbox — one per agent        │
 │                                         │      │ (Linux container from a pinned image)    │
 │  agent loop · approvals · audit         │      │                                          │
 │  model keys · connectors · policy       │ exec │  tool runner  ── shell, file tools       │
 │  tool definitions ─────────────────────────────►  (standard library only, read-only mount)│
 │                                         │stream│                                          │
 │  OpenShell provider                     │      │  session folders, same paths, rw / ro    │
 │   renders the policy, create / delete ──┼──┐   │  network: the allow list, nothing else   │
 │                                         │  │   │  no keys, no home folder, no state dir   │
 └─────────────────────────────────────────┘  │   └──────────────────────────────────────────┘
                                              ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────┐
 │ OpenShell gateway  — creates the sandbox, applies the policy (Landlock, seccomp,         │
 │ network), carries the exec stream, writes its own OCSF audit log                        │
 └─────────────────────────────────────────────────────────────────────────────────────────┘
```

OpenWorker uses OpenShell as it is. For each agent it creates a sandbox from a pinned base
image with a policy OpenWorker renders from the session: the folders, the user that owns
them, the network profile. Inside the sandbox runs OpenWorker's **tool runner**, a small
standard-library program that hosts the agent's shell and file tools. The desktop app or
the local server talks to it over OpenShell's own exec stream; no port is opened and
nothing is forwarded. Tool *definitions* stay in OpenWorker; only their *execution*
crosses into the sandbox, and everything that comes back is treated as data.

## What you need

- A Linux machine, or a Mac with Docker Desktop.
- Docker or Podman.
- OpenShell 0.1.2, the release OpenWorker is tested against.
- On a Mac: host networking turned on in Docker Desktop (**Settings ▸ Resources ▸
  Network ▸ Enable host networking**, then Apply & restart). OpenShell's sandboxes reach
  its gateway that way; with it off, no sandbox starts. The status check tells you when it
  is off.

### Coming from OpenShell 0.0.x

OpenShell 0.1 cannot be installed over 0.0.x, and the two cannot talk to each other.
Remove the old one first, following NVIDIA's
[upgrade guide](https://docs.nvidia.com/openshell/latest/upgrade/0-1-0): delete its
sandboxes, uninstall it, then run the setup below. The gateway settings file from 0.0.x is
not valid for 0.1; delete `~/.config/openshell/gateway.toml` so setup can write a new one.

## Set it up

```bash
openworker machine sandbox status
openworker machine sandbox setup
```

`status` lists what is missing. `setup` shows each change before it makes it and asks
first: it keeps the gateway running after you log out (`linger`, which needs `sudo` and
comes first, because NVIDIA's installer starts the gateway through your user's systemd
manager, and on WSL or a headless box that manager only runs once linger is on), runs
NVIDIA's installer for the pinned OpenShell release, allows bind mounts in the gateway's
configuration (that is how your folders reach a sandbox), sets the machine to use
OpenShell, and downloads the base image sandboxes are built from.

The base image is about 5 GB and is downloaded once. Until it is on the machine, Settings ▸
Sandbox shows OpenShell as **needs download** and a session is refused with the same
message, rather than hanging on the download. `setup` and the app's setup download it with Docker's own progress; the command is also `docker pull` of the image
named in the refusal.

In the app, Settings ▸ Sandbox shows OpenShell with a **Set up** button until it is ready.
The setup has three rows: **Docker Desktop is running** and **OpenShell is installed**,
which you fix yourself (each says what to do, with a link; the app installs neither), and
**Set up OpenShell for OpenWorker**, which the app does: it allows bind mounts, downloads
the base image with its progress, and sets the machine to use OpenShell. Each row gets a
tick as it finishes. The network choice and the shared credentials appear once a sandbox
type is chosen. OpenShell is not available on Windows yet.

The setting is per machine, in Settings ▸ Sandbox or in `config.toml`:

```toml
sandbox_provider = "openshell"      # or "direct": commands run in the OpenWorker process
sandbox_network_profile = "allowlist"  # or "open" (any site; files still confined)
sandbox_network_hosts = ["github.com:443", "pypi.org:443"]  # the sites you ticked; none by default
```

A project's own config cannot change it. When a machine is set to OpenShell and OpenShell
cannot be used (not installed, not running, bind mounts off), sessions are **refused**
with the reason, so a lost wall is never silent. A headless machine (`openworker up`, see
[Remote machines](remote-machines.md)) uses OpenShell on its own when it is installed and
running, and warns loudly when it has to run unprotected.

## What a sandbox gets

- The session's folders as bind mounts at the same absolute paths, read-only where the
  session has them read-only.
- The tool runner as a read-only mount, never baked into an image, started as the
  sandbox's main command so it lives as long as the sandbox.
- A policy with Landlock as a hard requirement: a kernel without it refuses to run
  instead of running open.
- The network profile below. No OpenShell credential providers are attached.

When a folder is added to a running session, the sandbox is recreated with the new folder
(filesystem rules are fixed when an OpenShell sandbox is created). Shell state is lost;
the agent is told.

## The network allow list

With OpenShell there is one choice:

- **Only the sites you allow** (`allowlist`, the default): nothing until you tick sites
  under **Choose sites…**. The list offers code hosting (GitHub, GitLab), the package
  registries (PyPI, npm, crates.io, the Go proxy) and the search APIs (Brave, Tavily,
  DuckDuckGo), each with a tick box, and you can add any site of your own.

**Allow everything** is not available with OpenShell. Its policy has to name each site, so
a session set to it does not start and says so.

A credential entry (below) also lets through the sites its tool needs.

The coworker's **web search** and **web fetch** tools run in the OpenWorker app, not in the
sandbox, and follow the same list. A site that is not on it asks you first; the card offers
once, this session, or **Always allow**, which adds the site to the list. With full access
(no approvals) the fetch is refused instead. "This session" and "Always allow" also open the
site for the session's commands, at once: the running sandbox takes the new entry without a
restart. "Once" is that one web call only. Connectors and MCP servers are not covered by the
list; each has its own approval.

When a command needs a site that is not on the list, it fails inside the sandbox. The
coworker can then ask you for the site: a card names it, gives the coworker's reason, and
says whether the sandbox has in fact blocked it. You choose **Allow for this session**,
**Always allow**, or **Deny**; the site opens for the running session without a restart.
The coworker is told which sites the sandbox blocked while a command ran, so it asks for the
right one. With full access (no approvals) the coworker cannot ask;
add the site in Settings ▸ Sandbox yourself.

The session's **Access** section lists its sites. Those you allowed for this session only
are marked **Session only**; you can take one back there, or allow another for the session.
Sites from Settings are changed in Settings ▸ Sandbox. A session site ends with the session.

OpenShell enforces the list in the container.

## Sharing a credential on purpose

By default the sandbox has none of your logins, which also means `git push` over SSH has
nothing to push with. Settings ▸ Sandbox ▸ **Explicit config and keys exposed to Agent**
lists only what you added, each with a switch; nothing is copied until you add it.
**Add… ▸ A CLI's login** offers these, marked found or not found on this machine:

| Entry | Copied from | What it is | Lets the agent | Hosts added to the allow list |
|---|---|---|---|---|
| SSH keys | `~/.ssh` | folder, credential | push and pull over SSH, and log in to servers, as you | `github.com:22`, `gitlab.com:22` |
| GitHub CLI | `~/.config/gh` | folder, credential | pull requests, issues and releases as you | `api.github.com:443`, `github.com:443` |
| AWS profiles | `~/.aws/config` | file, configuration | regions and profile names, no keys | `*.amazonaws.com:443` |
| AWS credentials | `~/.aws/credentials` | file, credential | your access keys | `*.amazonaws.com:443` |
| kubectl | `~/.kube/config` | file, credential | your clusters | the servers named in the kubeconfig |
| npm | `~/.npmrc` | file, credential | install and publish private packages | `registry.npmjs.org:443` |
| Docker registries | `~/.docker/config.json` | file, credential | push and pull images with the logins saved in the file (not those kept by a credential helper) | Docker Hub, `ghcr.io` |
| gcloud | `~/.config/gcloud` | folder, credential | your Google Cloud accounts and projects | `*.googleapis.com:443`, `accounts.google.com:443` |
| Terraform Cloud | `~/.terraform.d/credentials.tfrc.json` | file, credential | runs and state in Terraform Cloud as you | `app.terraform.io:443`, `registry.terraform.io:443`, `releases.hashicorp.com:443` |

Each one also sets the variable its tool reads to find the copy (`GH_CONFIG_DIR`,
`AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `KUBECONFIG`, `NPM_CONFIG_USERCONFIG`,
`DOCKER_CONFIG`, `CLOUDSDK_CONFIG`). **Add… ▸ A file or folder** adds anything else under
your home folder: a single file or a whole folder, labelled *credential* (a secret inside)
or *configuration* (host names, profiles, options), with the hosts its tool needs.

An enabled entry is **copied** into a private home that is mounted into the sandbox when
it starts, owner-only, and deleted with the sandbox; the real files are never opened for
writing. The copy lives under `~/.config/coworker/sandbox-runtime/`, never under `/tmp`:
the gateway NVIDIA's installer sets up runs with a private `/tmp` and could not mount a
folder there. For `ssh`, the copy is wired so that `ssh` and `git` inside use the copied keys
and known hosts, with no agent. Connectors always run in OpenWorker itself, outside every
sandbox, with their own tokens.

```toml
[[sandbox_credentials]]
name = "ssh"
enabled = true
```

## Security model

1. The tool runner inside the sandbox is untrusted: it holds no keys, decides nothing,
   and everything it returns is treated as data.
2. The sandbox holds the session's folders and nothing else of the machine; secrets are
   absent, not denied.
3. The network is an allow list, enforced by OpenShell.
4. The audit record comes from outside the wall: OpenWorker's tool-call events (with the
   enforcement level) plus OpenShell's own OCSF log.
5. When OpenShell cannot be used, the session is refused, never run open.

## Troubleshooting

- **"Sessions on this machine are refused"** — the machine is set to OpenShell and it
  cannot be used right now. Run `openworker machine sandbox status` for the reason; the
  usual ones are the gateway not running, bind mounts not allowed, or the base image not
  downloaded yet.
- **"Update Docker Desktop: this version's Linux kernel has no Landlock"** (a Mac) — OpenShell
  requires Landlock, and older Docker Desktop kernels are built without it (engine 28.0.4,
  kernel 6.10 had none; engine 29.8, kernel 7.0 has it). Update Docker Desktop, then check
  again. Setup asks Docker's kernel directly once the base image is downloaded.
- **"The sandbox base image is not downloaded yet"** — the one-time 5 GB download has
  not happened. Run `openworker machine sandbox setup` and accept the download, or run the
  `docker pull` command from the message; then start the session again.
- **A site is refused** — it is not ticked; tick or add it under **Choose sites…**, or add the
  credential entry whose hosts include it.
- **`git push` says permission denied inside the sandbox** — no credential is shared.
  Switch the `ssh` (or `gh`) entry on in Settings ▸ Sandbox.
