# Remote machines — run OpenWorker on another computer

Run the OpenWorker agent on a VM, home server, or spare machine, and control it
from the desktop app. Sessions live on the machine and **keep running while your
laptop is closed** — you reconnect and pick up where things stand.

A few things are true by design, before any setup:

- **Machines dial out to you.** A joined machine opens no ports and accepts no
  connections — it makes one outbound connection to your desktop app and holds
  it. There is nothing to expose, firewall, or harden on the machine.
- **Your data stays on the machine.** Conversations run and persist on the
  machine's own disk. The desktop shows them live while connected, and keeps a
  read-only copy of what you've viewed for when the machine is offline.
- **Each machine holds its own keys.** Model and connector keys you deploy are
  copied to the machine's local store, end-to-end encrypted to that machine —
  it works independently ever after.

## What you need

- The OpenWorker **desktop app** on your Mac or PC (this is the controller).
- A **Linux machine** (Ubuntu 22.04+ or similar, x64 or ARM) with Python 3.10+.
- A network path from the machine **to** your desktop — see
  [Reachability](#reachability-how-the-machine-finds-your-desktop) below.
  Tailscale is the easy answer; an SSH reverse tunnel works with nothing but
  OpenSSH.

## 1. Install on the machine

On the remote machine:

```bash
sudo apt update && sudo apt install -y git curl python3-venv python3-pip
python3 -m venv ~/ow-venv
~/ow-venv/bin/pip install "git+https://github.com/andrewyng/openworker.git"
```

Optional but recommended, so `openworker` is on your PATH:

```bash
echo 'export PATH=$HOME/ow-venv/bin:$PATH' >> ~/.bashrc && source ~/.bashrc
```

Check it:

```bash
openworker machine status
```

You should see `controller: not joined` — a fresh, unenrolled machine.

## Reachability: how the machine finds your desktop

The machine connects to your desktop app's local server (port 8765). Your
desktop binds to localhost only, so pick one of these:

**Option A — Tailscale (recommended).** Install [Tailscale](https://tailscale.com)
on both computers. In the desktop app, enable listening on the tailnet
interface, and use your desktop's Tailscale address in the join URL. Survives
reboots on both ends with no extra care.

**Option B — SSH reverse tunnel (zero new software).** If you can SSH from your
desktop to the machine, run this **on your desktop** and leave it running:

```bash
ssh -N -o ServerAliveInterval=15 -R 8765:localhost:8765 you@your-machine
```

This makes your desktop's server appear at the *machine's own*
`localhost:8765` — the join URL from the app works exactly as displayed.
Note: the tunnel dies if either end reboots; use `autossh` or a process
supervisor to keep it up unattended.

## 2. Enroll the machine

1. In the desktop app: **Settings → Machines → “Add a machine…”**. Opening the
   card *arms* enrollment: it mints a one-time join token valid for 10 minutes.
2. Leave **“Provision with my model keys”** ticked if you want the machine to
   receive your model keys the moment it joins (encrypted to the machine; see
   [Keys](#3-give-the-machine-keys)).
3. Copy the one-line command from the card and run it on the machine:

```bash
openworker join http://127.0.0.1:8765/j/<token> --name=my-box
```

The card flips to **“✓ my-box joined”** and shows the machine's key
fingerprint. Verify it matches `openworker machine status` on the machine — that
fingerprint is the machine's permanent identity. The token is single-use and
already dead; reconnects authenticate with the machine's own keypair, so you
never enroll twice.

## 3. Give the machine keys

The machine needs its own model key to run turns (its own keys, its own calls —
your desktop is not in the loop at runtime). Three ways:

- **At enrollment**: the “Provision with my model keys” checkbox on the card.
- **Anytime, from the desktop**: every provider card in **Settings → Models**
  (and key-based connector cards) shows an **“Available on”** row once a
  machine is enrolled — click the machine's chip to deploy or remove that key.
  If you rotate a key later, the chip shows ↻; click to redeploy.
- **On the machine itself**, if you'd rather keys never transit anywhere:

```bash
openworker machine keys set provider:openai api_key=sk-...
```

  (Or inject via environment/secret-manager — the machine's store resolves
  `${VAR}` references.)

Deployed keys are sealed to the machine's public key before they leave your
desktop; only that machine can decrypt them. OAuth-connected accounts (Google,
Slack, …) are *not* copied between machines — each machine connects its own.

## 4. Use it

- **Start a session on the machine**: in the composer's setup row, the **runs
  on** chip appears once a machine is enrolled — pick ⌂ my-box. The model
  picker switches to the machine's own models.
- **Folder-based coworkers** (code work, leads): when you send, you'll be
  asked where on the machine to work — type a path (validated by the machine
  itself), pick one of its recent folders, or start in a temporary folder
  created on the machine.
- **Sidebar**: remote sessions wear a quiet ⌂ badge; the RECENT header's
  group-and-filter popover gains a **Machine** grouping.
- **Inbox**: approvals and questions parked by sessions on any connected
  machine appear in your desktop Inbox, tagged with the machine — answer them
  from your chair.
- **Machine offline?** Its sessions stay listed (greyed). Conversations you
  had opened remain readable, read-only, with a banner; new messages and
  approvals wait for the machine to return. It reconnects on its own.

## 5. Run it as a service

To keep the machine serving across logouts and reboots, on the machine:

```bash
openworker machine service install
```

This writes and enables a systemd user unit running `openworker up`
(auto-restart, 5s backoff). To also run without anyone logged in:

```bash
sudo loginctl enable-linger $USER
```

`openworker machine service uninstall` reverses it. (If you used the SSH-tunnel
option, remember the tunnel on your desktop needs the same treatment —
`autossh` or a LaunchAgent — or the machine will wait patiently for a tunnel
that isn't there.)

## Security model, in five lines

- The machine only dials **out**; it listens on nothing.
- First contact is authorized by a **single-use token** you minted seconds
  earlier; every reconnect is a challenge signed by the machine's private key.
- The controller **pins** the machine's public keys at enrollment and refuses
  a second concurrent connection claiming the same identity.
- Secrets you deploy are **encrypted to the machine** before leaving your
  desktop.
- Conversation content lives on the machine; your desktop caches only what
  you've viewed, and only for offline reading.

One more thing worth knowing: signing in to OpenWorker Cloud never changes
what's on this computer. Your conversations, keys, and enrolled machines
belong to this computer's user account; sign-in only adds cloud machines
and cloud features on top, and signing out hides only those.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `join` says `not-enrolled` | Token expired (10 min) or already used — reopen the Add-a-machine card for a fresh URL. |
| `join` says `already-connected` | Another process holds this machine's identity (a copied state dir, or a service already running). One identity, one connection. |
| `join` says `protocol-mismatch` | Desktop and machine versions differ too much — update the older side. |
| Machine shows offline after its reboot | The service reconnects by itself within seconds — if it doesn't, an SSH tunnel on the desktop probably died with it. |
| Turn fails with a key error | The machine resolves models with **its** keys — deploy one (§3) or check the machine's model settings via the session's model picker. |

State on the machine lives in `~/.config/coworker` (identity keys, secrets,
conversations). `openworker machine leave` forgets the enrollment *and* the identity —
re-joining afterwards needs a fresh join URL, and the old entry can be removed
from the desktop's Machines page.
