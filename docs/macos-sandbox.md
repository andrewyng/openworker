# The macOS sandbox — run an agent's commands behind a wall, with nothing to install

On a Mac, OpenWorker can run a session's shell commands and file tools inside the sandbox
that is built into macOS (Seatbelt, the same mechanism the system uses for its own apps).
There is nothing to install: OpenWorker renders a profile from the session's folders and
starts its tool runner under it. The kernel enforces the profile, every process a command
starts inherits it, and the agent loop, your model keys and every connector stay outside.

A few things are true by design:

- **Files: only the session's folders.** A command can read and write the session's
  writable folders and one private temporary folder, and read its read-only folders. The
  rest of your home folder — `.ssh`, `.aws`, browser profiles, documents, OpenWorker's own
  state and its API token — cannot be read or even listed.
- **Network: only an allow list.** The sandbox may reach exactly one local port, an
  allow-list proxy that OpenWorker runs; the proxy tunnels to the hosts on the profile
  and refuses everything else, naming the host.
- **Secrets are absent, not denied.** Nothing in the sandbox holds a key, and variables
  in OpenWorker's environment whose names say they hold a secret are not passed in.
- **The wall is checked before the session starts.** OpenWorker verifies from inside that
  every session folder is reachable and that your home folder is **not**; if the wall is
  not up, the session is refused rather than run open.
- **Every tool result says which mode produced it.** The enforcement level is recorded on
  each tool-call event and in the audit trail.
- **Nothing changes until you choose.** The desktop app runs commands directly, as it
  always has, until you turn the sandbox on for the machine.

## Turn it on

Settings ▸ Sandbox ▸ "macOS sandbox", or in `config.toml`:

```toml
sandbox_provider = "seatbelt"       # or "direct": commands run in the OpenWorker process
sandbox_network_profile = "allowlist"  # or "open" (any site; files still confined)
sandbox_network_hosts = ["github.com:443", "pypi.org:443"]  # the sites you ticked; none by default
```

The setting is per machine; a project's own config cannot change it.

## What a command can reach

Read and write:

- the session's writable folders;
- one private folder under `/tmp`, which holds the runner's socket, temporary files, the
  tool caches (`npm`, `pip`, `uv`, Go, Yarn are pointed there) and the sandbox's own home.

Read only:

- the session's read-only folders;
- the system: `/usr`, `/bin`, `/System`, `/Library`, `/opt`, `/Applications`, `/private/etc`;
- OpenWorker's managed tools folder, the tool runner and its Python;
- under your home folder, only the developer toolchains you switch on in Settings ▸
  Sandbox — offered when your Mac has them: `.nvm`, `.volta`, `.bun`, `.deno`, `.pyenv`,
  `.rbenv`, `.asdf`, `.sdkman`, `.cargo`, `.rustup`, `.local/bin`, `.local/share/uv`,
  `.local/share/mise`, `.local/pipx`, `go`; all off until you switch one on, and you can
  add a folder — plus git's settings (`.gitconfig`, `.config/git`).

Network: `localhost` on the proxy's port, nothing else. `curl`, `git`, `pip` and `npm`
follow the proxy variables; a program that ignores them has no network at all.

## The network allow list

Two choices, shared with the other sandboxes:

- **Only the sites you allow** (`allowlist`, the default): nothing until you tick sites
  under **Choose sites…**. The list offers code hosting (GitHub, GitLab), the package
  registries (PyPI, npm, crates.io, the Go proxy) and the search APIs (Brave, Tavily,
  DuckDuckGo), each with a tick box, and you can add any site of your own.
- **Allow everything** (`open`): any site, no proxy. The files are still the wall.

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

An enabled entry is **copied** into the sandbox's private home when the sandbox starts,
owner-only, and deleted with the sandbox; the real files are never opened for writing.
For `ssh`, the copy is wired so that `ssh` and `git` inside use the copied keys and known
hosts through the proxy, with no agent.

Logins kept in the macOS Keychain (git over HTTPS) are not files and cannot be shared;
use an SSH remote, `gh`, or the GitHub connector. Connectors always run in OpenWorker
itself, outside every sandbox, with their own tokens.

## Known limits

- `ps` does not run inside the sandbox, and `pgrep` cannot list processes.
- A toolchain kept somewhere under your home folder that is not on the list above does not
  run until it is added.
- `cargo` cannot download crates: its registry lives under the read-only `~/.cargo`.
- When a folder is added to a running session, the sandbox is restarted with the new
  folder (a profile is fixed when a process starts). Shell state is lost; the agent is told.

## Security model

1. The tool runner inside the sandbox is untrusted: it holds no keys, decides nothing, and
   everything it returns is treated as data.
2. The sandbox holds the session's folders and nothing else of your home; secrets are
   absent, not denied.
3. The network is an allow list, enforced by the proxy; the refusal names the host.
4. The audit record comes from outside the wall: OpenWorker's tool-call events, each with
   the enforcement level.
5. When the wall cannot be proved up, the session is refused, never run open.

## Troubleshooting

- **"Sessions on this machine are refused"** — the machine is set to the sandbox and it
  cannot be used, usually because OpenWorker itself is running inside another sandbox
  (macOS does not nest them). `openworker machine sandbox status` says why.
- **A tool cannot be found inside the sandbox** — it lives somewhere under your home folder
  that is not on the toolchain list.
- **A site is refused** — it is not ticked; tick or add it under **Choose sites…**, or add the
  credential entry whose hosts include it.
- **`git push` says permission denied inside the sandbox** — no credential is shared.
  Switch the `ssh` (or `gh`) entry on in Settings ▸ Sandbox.
