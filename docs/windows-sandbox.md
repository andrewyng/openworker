# The Windows sandbox — run an agent's commands as an account that cannot see your files

On Windows, OpenWorker can run a session's shell commands and file tools as a hidden local
account that Windows itself keeps out of your profile. There is nothing to install: one
setup step, run once with administrator rights, creates the account and its rules. After
that every command an agent runs, and every process those commands start, runs as that
account, in a job that ends with the session. The agent loop, your model keys and every
connector stay outside, in OpenWorker's own process, as you.

The wall is the operating system's own: file permissions, the Windows Firewall, and
Windows Filtering Platform filters, all written once at setup. Nothing is enforced by
environment variables alone.

```
 your machine
 ┌──────────────────────────────────────────┐      ┌───────────────────────────────────────────┐
 │ OpenWorker (desktop app or local server) │      │ the sandbox — one per agent session       │
 │ runs as YOU                              │      │ runs as a hidden local account            │
 │                                          │      │                                           │
 │  agent loop · approvals · audit          │ named│  tool runner ── PowerShell, file tools    │
 │  model keys · connectors · policy        │ pipe │  every process it starts inherits the     │
 │  tool definitions ───────────────────────────────►  account, the job and the rules          │
 │                                          │      │                                           │
 │  Windows provider                        │      │  session folders, same paths, rw / ro     │
 │   grants the folders, starts the runner, │      │  a private folder for temp and caches     │
 │   proves the wall before the session ────┼──┐   │  your profile: invisible                  │
 │                                          │  │   │  network: by account (below)              │
 │  allow-list proxy (closed mode only)     │  │   │  no keys, no OpenWorker state, no token   │
 └──────────────────────────────────────────┘  │   └───────────────────────────────────────────┘
                                               ▼
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │ Windows — permissions on your folders (one entry per session folder), the profile boundary │
 │ (another account cannot read C:\Users\you), a firewall rule and loopback filters on the    │
 │ closed account, a job object that ends every sandboxed process with the session            │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

A few things are true by design:

- **Files: your profile is out of reach.** `C:\Users\<you>` — `.ssh`, `.aws`, documents,
  browser profiles, OpenWorker's own state and its API token — cannot be read or listed by
  the sandbox account. A command can read and write the session's writable folders and one
  private folder of its own, and read its read-only folders.
- **Network: you choose, and the choice is an account.** Open (the default on Windows) runs
  commands as an account with no network rules. Strict and Standard run them as a second
  account whose only way out is OpenWorker's allow-list proxy: the firewall blocks its
  outbound traffic and kernel filters close every local port to it except the proxy's.
- **Secrets are absent, not denied.** The sandbox account has a profile of its own with
  nothing in it. Credentials you share on purpose are copied in for the session and
  removed with it.
- **The wall is checked before the session starts.** OpenWorker verifies from inside that
  every session folder is reachable, that your profile is **not**, and in the closed mode
  that a local port outside the proxy's range is unreachable. If any check fails the
  session is refused rather than run open.
- **Every tool result says which mode produced it.** The enforcement level is recorded on
  each tool-call event and in the audit trail.
- **Nothing changes until you choose.** OpenWorker runs commands directly, as it always
  has, until you turn the sandbox on for the machine.

## Turn it on

Settings ▸ Sandbox has one switch, "Run agents in a sandbox". Turning it on shows the
sandbox types this PC can use. Until the one-time setup has run, "Windows sandbox" looks
disabled with a **Set up** button, which shows what setup changes and runs it on one
administrator prompt. Once the type is chosen its options appear: the network, the config
and keys exposed to agents, and the tools shown to agents with read-only access. In `config.toml`:

```toml
sandbox_provider = "windows"        # or "direct": commands run in the OpenWorker process
sandbox_network_profile = "open"    # the Windows default; or "allowlist"
sandbox_network_hosts = ["github.com:443"]  # for "allowlist": the sites you ticked
```

The setting is per machine; a project's own config cannot change it.

Setting up the Windows sandbox also chooses it (the command line form is
`openworker machine sandbox setup`). Setup ends by opening a throwaway sandbox and proving
the wall from inside; only then does the choice take effect. Until setup has run, the type
cannot be chosen and the switch goes back off. There is no weaker mode. If setup is later
found broken (an account deleted, the rule or a filter gone), sessions on the machine are
refused with the way to set it up again, rather than run without the sandbox. Switching the
sandbox off keeps the setup on the PC; "Remove setup" under the type undoes it.

Someone who is not an administrator sees the type with the command to hand to one.

## What the setup does

One elevated PowerShell script, one prompt. It:

1. creates two hidden local accounts with random passwords nobody sees, `OWSandboxOpenNet`
   and `OWSandboxClosedNet`, hidden from the sign-in screen, with remote, network and
   service logon denied;
2. writes one Windows Firewall rule that blocks every outbound connection for the closed
   account;
3. writes four Windows Filtering Platform filters for the closed account: on loopback it may
   reach ports 47800–47899, where OpenWorker's proxy listens, and nothing else. Windows
   Firewall does not look at loopback traffic; these filters do;
4. stores the passwords in `C:\ProgramData\OpenWorker\sandbox\account.cred`, readable by
   you, administrators and the system only;
5. makes `C:\ProgramData\OpenWorker\sandbox\sandboxes`, where each session gets a private
   folder;
6. records everything it changed in `setup.json`, so that `openworker machine sandbox
   remove` undoes exactly that.

`openworker machine sandbox status` shows each of these and what is missing.

## The network modes

```
                          Settings ▸ Sandbox ▸ Network
                                      │
             ┌────────────────────────┴────────────────────────┐
             │                                                 │
     Allow everything (default)                Only the sites you allow
             │                                                 │
             ▼                                                 ▼
   runs as OWSandboxOpenNet                          runs as OWSandboxClosedNet
   no firewall rule, no filters                      firewall: all outbound BLOCKED
   any host, any local port                          filters: loopback closed except
   files still confined                                       the proxy's port range
             │                                                 │
             ▼                                                 ▼
          internet                     ┌──────────────────────────────────────────┐
                                       │ OpenWorker's allow-list proxy (loopback) │
                                       │ CONNECT github.com:443      ✓            │
                                       │ CONNECT pypi.org:443        ✓            │
                                       │ CONNECT attacker.example:443 ✗ 403       │
                                       └──────────────────────────────────────────┘
                                                            │
                                                            ▼
                                                     only listed hosts
```

- **Allow everything** (`open`) — any host. Nothing to configure. The files are still the
  wall. This is the default on Windows, and the page shows it in amber.
- **Only the sites you allow** (`allowlist`) — the sites you tick under **Choose sites…**
  (code hosting, package registries, search APIs, or any site you add), through the proxy;
  nothing until you tick some. A program that ignores the proxy variables has no network
  at all, because the account's direct traffic is blocked in the kernel.

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

Choosing a mode never touches the firewall. The rules are written once at setup; OpenWorker
picks the account. In the closed mode a sandbox can still **listen** on a local port (a dev
server your browser can open); it cannot **connect** to one, other than the proxy.

## What a command can reach

Read and write:

- the session's writable folders (one permission entry each, removed when the session ends);
- one private folder under `C:\ProgramData\OpenWorker\sandbox\sandboxes`, which holds the
  runner, temporary files and the tool caches.

Read only:

- the session's read-only folders;
- Windows itself and machine-wide installs (`C:\Windows`, `C:\Program Files`, and what any
  local account may read);
- folders **outside your profile**, such as `D:\work` or `C:\src`. Windows lets every local
  account read those by default, and so can the sandbox. Keep what the agent must not see
  under your profile.

Passed through, not listed: the folders between a granted folder and your profile root.
A project under `C:\Users\<you>\code` needs the shell to step through `C:\Users\<you>`
and `code`; each gets a traverse-only entry for the session, which lets nothing be listed
or read there.

Read only, from your profile, so the agent can run your tools:

- developer tools installed per user, from a list you control in Settings ▸ Sandbox:
  nvm for Windows and npm's global folder (`AppData\Roaming`), pyenv-win, per-user
  Python installs, Scoop, Cargo, rustup, Go, pipx and uv. The page lists only the ones
  this PC has, all off until you switch one on; you can add a folder. The session also inherits your `PATH`, so those tools resolve. Nothing under
  them is writable.

Not readable:

- the rest of your profile, `C:\Users\<you>`, and every other account's profile.

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
| gcloud | `~/AppData/Roaming/gcloud` | folder, credential | your Google Cloud accounts and projects | `*.googleapis.com:443`, `accounts.google.com:443` |
| Terraform Cloud | `~/AppData/Roaming/terraform.d/credentials.tfrc.json` | file, credential | runs and state in Terraform Cloud as you | `app.terraform.io:443`, `registry.terraform.io:443`, `releases.hashicorp.com:443` |

Each one also sets the variable its tool reads to find the copy (`GH_CONFIG_DIR`,
`AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `KUBECONFIG`, `NPM_CONFIG_USERCONFIG`,
`DOCKER_CONFIG`, `CLOUDSDK_CONFIG`). **Add… ▸ A file or folder** adds anything else under
your home folder: a single file or a whole folder, labelled *credential* (a secret inside)
or *configuration* (host names, profiles, options), with the hosts its tool needs.

A copy is written into the sandbox account's own profile by the runner, so that Windows
OpenSSH accepts the key's permissions, and removed when the runner leaves. In the closed
mode `ssh` reaches its hosts through the proxy; OpenWorker ships the tunnel command, since
Windows has no `nc`. The agent is told what it can do ("you can push over SSH as the
user"), never where a credential is kept. Short-lived cloud roles (AWS first) and an
encrypted wallet for keys that cannot be short-lived are the next additions; see the
design notes.

## Every agent, one account

All sessions run under the same account (one per network mode). The goal is to keep your
data away from the agents, not to keep agents away from each other: a sandbox can read
another sandbox's private folder, including credentials copied in for it. Cloud roles that
expire on their own limit what that is worth.

## Known limits

- The `open` mode is open: any host, any local port, including services on this machine.
- `curl.exe` needs `--ssl-revoke-best-effort` in the closed mode, because Windows' own TLS
  checks certificate revocation over plain HTTP, which the firewall blocks. Git, Python and
  Node use OpenSSL and are unaffected. PowerShell 5's `Invoke-WebRequest` ignores the proxy
  variables and sees no network in the closed mode; `curl.exe` follows them.
- OpenShell is not available on Windows today (its Windows driver is a preview of
  Microsoft's execution containers). The Windows sandbox is OpenWorker's own.

## How it compares

| | Windows sandbox | macOS sandbox | OpenShell |
|---|---|---|---|
| Mechanism | a second local account, permissions, firewall and kernel filters | the Seatbelt profile built into macOS | a Linux container with Landlock and seccomp |
| Install | one administrator prompt | nothing | OpenShell and Docker |
| Files | session folders; profile invisible except the tool list; outside-profile folders readable | session folders; home invisible except the tool list | session folders; nothing else |
| Network | open (default), or the allow list through the proxy | the allow list through the proxy, or open | the allow list in the policy, or open |
| Default | open | only the sites you allow | only the sites you allow |
