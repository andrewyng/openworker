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
sandbox_network_profile = "strict"  # or "standard", or "open" (any host; files still confined)
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
- under your home folder, only the developer toolchains on the list in Settings ▸
  Sandbox — shipped: `.nvm`, `.volta`, `.bun`, `.deno`, `.pyenv`, `.rbenv`, `.asdf`,
  `.sdkman`, `.cargo`, `.rustup`, `.local/bin`, `.local/share/uv`, `.local/share/mise`,
  `.local/pipx`, `go`; each with a switch, and you can add a folder — plus git's settings
  (`.gitconfig`, `.config/git`).

Network: `localhost` on the proxy's port, nothing else. `curl`, `git`, `pip` and `npm`
follow the proxy variables; a program that ignores them has no network at all.

## The network allow list

Three profiles, shared with the OpenShell and Windows sandboxes:

- **strict** (default): GitHub, GitLab, and the package registries — PyPI, npm, crates.io,
  the Go proxy.
- **standard**: strict plus the search APIs (Brave, Tavily, DuckDuckGo).
- **open**: any host, no proxy. The files are still the wall.

Credentials shared on purpose (below) add the hosts their tools need.

## Sharing a credential on purpose

By default the sandbox has none of your logins, which also means `git push` over SSH has
nothing to push with. Settings ▸ Sandbox lists files you can share, all off by default:

| Entry | Copied from | Lets the agent | Hosts added to the allow list |
|---|---|---|---|
| `ssh` | `~/.ssh` | push and pull over SSH, and log in to servers, as you | `github.com:22`, `gitlab.com:22` |
| `gh` | `~/.config/gh` | use `gh` as you: pull requests, issues, releases | `api.github.com:443`, `github.com:443` |
| `aws` | `~/.aws/config` | use `aws` with your profiles; `~/.aws/credentials` stays out unless you add it | `*.amazonaws.com:443` |
| `kube` | `~/.kube/config` | use `kubectl` with your clusters | the servers named in the kubeconfig |

An entry is a single file or a whole folder, your choice; each is labelled *credential* or
*configuration*. Settings ▸ Sandbox shows them once the sandbox is on: one switch first,
then the type, then the type's own options.

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
- **A host is refused** — it is not on the profile; switch to `standard` if it is a search
  API, or share the credential entry whose hosts include it.
- **`git push` says permission denied inside the sandbox** — no credential is shared.
  Switch the `ssh` (or `gh`) entry on in Settings ▸ Sandbox.
