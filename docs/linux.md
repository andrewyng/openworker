# OpenWorker on Linux

The desktop app runs on Linux the same way it runs on macOS and Windows: a Tauri shell that
supervises the Python agent server as a bundled sidecar. Two formats are produced —

| Format | For | Updates |
|---|---|---|
| `.deb` | Debian, Ubuntu, and **ChromeOS Crostini** | your package manager |
| `.AppImage` | any distro, no root needed | in-app auto-update |

Everything the agent actually does — the engine, connectors, MCP, the terminal and file tools,
automations, Slack — is the same Python server on every platform, so it behaves identically
here. The differences are all in the desktop shell, and they are listed under
[Platform differences](#platform-differences) below.

## Install

### .deb (Debian / Ubuntu / Crostini)

```shell
sudo apt install ./OpenWorker_<version>_<arch>.deb
```

`apt` (rather than `dpkg -i`) so the WebKit/GTK dependencies come in with it. On ChromeOS you
can also just double-click the file in the Files app.

### AppImage

```shell
chmod +x OpenWorker_<version>_<arch>.AppImage
./OpenWorker_<version>_<arch>.AppImage
```

AppImages need FUSE, which minimal systems (Crostini included) don't ship. Either install it
(`sudo apt install libfuse2`) or skip it entirely:

```shell
./OpenWorker_<version>_<arch>.AppImage --appimage-extract-and-run
```

Only the AppImage self-updates. A `.deb` install never shows an update prompt, because the app
must not write into paths `dpkg` owns — upgrade it by installing the newer `.deb`.

## ChromeOS (Crostini)

Crostini is a Debian 12 container, so the `.deb` is the path of least resistance.

1. **Turn Linux on**: Settings → About ChromeOS → Developers → *Linux development environment*.
2. **Pick the right architecture**: run `dpkg --print-architecture` in the Linux terminal —
   `amd64` on Intel/AMD Chromebooks, `arm64` on most MediaTek/Qualcomm ones.
3. **Install**: download the `.deb`, then double-click it in the Files app (or
   `sudo apt install ./OpenWorker_*.deb` in the terminal).
4. **Launch**: OpenWorker appears in the ChromeOS launcher under *Linux apps*.

### Giving it access to your files

The Linux container has its own home directory — the one you see in Files under *Linux files*.
That is what OpenWorker can reach by default. ChromeOS folders (Downloads, Google Drive,
external drives) are invisible to it until you share them: right-click the folder in Files →
**Share with Linux**. It then shows up in the container at `/mnt/chromeos/MyFiles/<folder>`,
and you can point OpenWorker's workspace there.

This is a ChromeOS security boundary, not an OpenWorker limitation — every Linux app on the
device sees exactly the same thing.

### What to expect on ChromeOS specifically

- **No system tray.** ChromeOS has no status area for Linux apps, so closing the window quits
  the app instead of hiding it (elsewhere on Linux, if a tray is available, close hides to it).
  Scheduled automations only run while the app is open.
- **Blank white window?** Fixed automatically. WebKitGTK's default renderer draws nothing
  through Crostini's virtualized GPU; the shell detects Crostini at startup and turns that
  renderer off (`WEBKIT_DISABLE_DMABUF_RENDERER`). Set the variable yourself to override.
- **"Keep this system awake" won't stick.** ChromeOS decides when the device sleeps, and a
  suspended Chromebook suspends the whole Linux VM with it. The toggle reports off rather than
  claiming a hold it can't take.
- **Voice Input is absent** — see below.
- It is a VM on modest hardware: expect the app to feel heavier than it does on a laptop.

## Platform differences

| | macOS / Windows | Linux |
|---|---|---|
| Agent server, connectors, MCP, tools | ✅ | ✅ identical |
| Automations / scheduler | ✅ | ✅ (while the app runs) |
| System tray, close-to-tray | ✅ | where the desktop provides one |
| Open at login | ✅ | ✅ (XDG autostart) |
| Keep system awake | caffeinate / Win32 | `systemd-inhibit` when present |
| Native folder picker | ✅ | ✅ (GTK; the server-side picker needs `zenity` or `kdialog`) |
| In-app auto-update | ✅ | AppImage only |
| **Voice Input (dictation)** | ✅ | ❌ not built |

**Why no Voice Input.** The engine (`stt/`) compiles whisper.cpp and links ALSA, which would
add `cmake`, a C++ toolchain and ALSA headers to every Linux build — a real cost on every
checkout for a feature the released Linux artifacts don't carry. The Tauri shell links a stub
instead (`dictation_stub` in `surfaces/gui/src-tauri/src/lib.rs`) and the mic button stays
disabled. Nothing else in the app is affected. Turning it on later is a small change: drop the
`cfg(not(target_os = "linux"))` guard on the `ocw-stt` dependency in `Cargo.toml`.

## Build from source

### The one-command way

```shell
git clone https://github.com/andrewyng/openworker
cd openworker
bash packaging/bootstrap_linux.sh
```

That installs everything a build needs, skipping whatever you already have: the WebKit/GTK
system libraries (apt or dnf, via `sudo`), the Rust toolchain, Node 20 if yours is older,
the Python venv at `.venv`, and the GUI's npm packages. It prints the plan and asks before
touching anything — `--yes` skips the prompt, `--packaging` also installs the extra Python
deps `build_linux.sh` needs.

It installs Rust and Node with the official rustup/nvm installers, which append to your shell
profile. Prefer your distro's packages? Install those two yourself first; the script detects
them and moves on.

Budget **~10 GB of free disk** — `surfaces/gui/src-tauri/target` alone reaches 5.8 GB. On
ChromeOS: Settings → Advanced → Developers → Linux → *Disk size*. The script warns if you're
short before it starts.

Then:

```shell
cd surfaces/gui && npm run tauri dev     # the real desktop shell — window + server
```

### Doing it by hand

Prerequisites: Python 3.10+, Node 20+ (Debian 12 ships 18), the Rust toolchain via
[rustup](https://rustup.rs/), and the Tauri system libraries.

```shell
# Debian 12 / Ubuntu 22.04+ (Crostini included)
sudo apt install build-essential pkg-config curl wget file git \
                 python3-venv python3-dev \
                 libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev \
                 libayatana-appindicator3-dev libssl-dev patchelf

# Fedora
sudo dnf install gcc gcc-c++ make pkgconf-pkg-config python3-devel \
                 webkit2gtk4.1-devel gtk3-devel librsvg2-devel \
                 libappindicator-gtk3-devel openssl-devel patchelf
```

Then the same three steps as every other platform:

```shell
bash packaging/setup_dev_env.sh          # 1. Python venv at .venv

.venv/bin/openworker-server --cwd ~/some/project --port 8765   # 2. the server

cd surfaces/gui && npm install && npm run dev                  # 3. the UI
```

`npm run tauri dev` (from `surfaces/gui/`) runs the real desktop shell instead of the browser
UI — it launches the window and starts the server itself.

### Installable packages

```shell
bash packaging/build_linux.sh
```

Produces `.deb` and `.AppImage` under `surfaces/gui/src-tauri/target/release/bundle/`. It needs
the build-only Python deps in the venv first (`bootstrap_linux.sh --packaging` does this for
you):

```shell
.venv/bin/pip install -e '.[bedrock]' pyinstaller typer
```

Set `OCW_LINUX_BUNDLES` to change the formats (`deb`, `appimage`, `rpm`, comma-separated).

**Build on the oldest distro you want to support.** The frozen sidecar and the Rust binary both
link the build machine's glibc, and glibc is forward- but not backward-compatible: artifacts
built on Ubuntu 24.04 (glibc 2.39) will not start on Debian 12 (glibc 2.36), which is what
Crostini runs. Release CI builds on ubuntu-22.04 (glibc 2.35) for that reason.

## Troubleshooting

**The window is blank / white.** WebKitGTK's DMABuf renderer against a virtualized or unusual
GPU. Crostini is handled automatically; elsewhere, run with
`WEBKIT_DISABLE_DMABUF_RENDERER=1 openworker` (or `WEBKIT_DISABLE_COMPOSITING_MODE=1`).

**Nothing happens after the splash / "Starting coworker…" never finishes.** The sidecar failed
to start. Its log is at `~/.config/coworker/logs/openworker-server.log` (previous run:
`.log.old`).

**"Choose folder" does nothing when running the browser UI.** The server-side folder picker
shells out to `zenity` or `kdialog`; with neither installed, paste the path into the workspace
field instead. `sudo apt install zenity` restores the dialog. The desktop app uses GTK's own
picker and is unaffected.

**The app installs but doesn't appear in the launcher.** Log out and back in (or restart the
Crostini container: `sudo systemctl reboot` in the Linux terminal) — the desktop file is picked
up on session start.

**Tools the agent installed aren't found.** A desktop-launched app doesn't inherit your shell's
`PATH`. The shell probes your login shell at startup and merges its environment in, and adds
the usual install dirs (`~/.local/bin`, `~/.cargo/bin`, `~/go/bin`, `/snap/bin`, Linuxbrew) on
top. If a tool still isn't visible, launch the app from a terminal once to confirm it's a `PATH`
problem rather than a missing install.
