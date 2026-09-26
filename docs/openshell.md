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
- OpenShell 0.0.116, the release OpenWorker is tested against.

## Set it up

```bash
openworker machine sandbox status
openworker machine sandbox setup
```

`status` lists what is missing. `setup` shows each change before it makes it and asks
first: it runs NVIDIA's installer for the pinned OpenShell release, allows bind mounts in
the gateway's configuration (that is how your folders reach a sandbox), keeps the gateway
running after you log out, and sets the machine to use OpenShell. It never runs `sudo` for
you; when a step needs an administrator, it prints the command.

The setting is per machine, in Settings ▸ Sandbox or in `config.toml`:

```toml
sandbox_provider = "openshell"      # or "direct": commands run in the OpenWorker process
sandbox_network_profile = "strict"  # or "standard"
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

Two profiles:

- **strict** (default): GitHub, GitLab, and the package registries — PyPI, npm, crates.io,
  the Go proxy.
- **standard**: strict plus the search APIs (Brave, Tavily, DuckDuckGo).

OpenShell enforces the list in the container. Credentials shared on purpose (next
section) add the hosts their tools need.

## Sharing a credential on purpose

By default a sandbox has none of your logins, which also means `git push` over SSH has
nothing to push with. If you want a tool to work inside as it does outside, you can share
specific files. Settings ▸ Sandbox lists them, all off by default:

| Entry | Copied from | Lets the agent | Hosts added to the allow list |
|---|---|---|---|
| `ssh` | `~/.ssh` | push and pull over SSH, and log in to servers, as you | `github.com:22`, `gitlab.com:22` |
| `gh` | `~/.config/gh` | use `gh` as you: pull requests, issues, releases | `api.github.com:443`, `github.com:443` |
| `aws` | `~/.aws` | use `aws` with your profiles | `*.amazonaws.com:443` |
| `kube` | `~/.kube` | use `kubectl` with your clusters | the servers named in the kubeconfig |

You can add your own entries (a name, a path under your home folder, the hosts it needs).

An enabled entry is **copied** into a private home that is mounted into the sandbox when
it starts, owner-only, and deleted with the sandbox; the real files are never opened for
writing. For `ssh`, the copy is wired so that `ssh` and `git` inside use the copied keys
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
  usual ones are the gateway not running, or bind mounts not allowed.
- **A host is refused** — it is not on the profile; switch to `standard` if it is a search
  API, or share the credential entry whose hosts include it.
- **`git push` says permission denied inside the sandbox** — no credential is shared.
  Switch the `ssh` (or `gh`) entry on in Settings ▸ Sandbox.
