# Linux Local Smoke Test - 2026-08-25

Environment:
- OS: Ubuntu 24.04.4 LTS (Noble)
- Python: 3.11.9
- Node: 22.22.0
- npm: 10.9.4
- Rust: rustc 1.95.0-nightly

## Result

OpenWorker works locally on Linux for the Python server and browser GUI from source.
The initial Linux desktop `.deb` build also works from a fresh Ubuntu 24.04 VM
after installing the package prerequisites added in this PR.

Verified:
- `bash packaging/setup_dev_env.sh` completed successfully.
- `npm install` completed successfully in `surfaces/gui`.
- Server started with `.venv/bin/openworker-server --cwd /home/luis.lobo@epicio.com/dev/luislobo/openworker --port 8765`.
- Server endpoints responded with valid JSON using the sidecar token:
  - `/v1/health`
  - `/v1/settings`
  - `/v1/sessions`
  - `/v1/personas`
- Browser UI started with `npm run dev -- --host 127.0.0.1` and served `http://127.0.0.1:1420/`.
- GUI production build passed with `npm run build`.
- GUI unit tests passed: 134 tests.
- GUI E2E tests passed on Linux Chromium: 221 tests.
- Backend tests passed after installing Bedrock optional dependencies: 1,861 passed, 1 skipped.
- Barebones Ubuntu 24.04 VM smoke passed from a fresh `.venv` and fresh
  `node_modules`:
  - `bash packaging/setup_dev_env.sh`
  - `.venv/bin/pytest tests/test_config.py tests/test_environment.py tests/test_bedrock_provider.py -q`
  - `npm install`
  - `npm test -- --run`
  - `npm run build`
  - `packaging/build_linux.sh deb`
- VM Linux bundle produced:
  `surfaces/gui/src-tauri/target/release/bundle/deb/OpenWorker_0.2.1_amd64.deb`
- Local `.deb` install passed with `sudo apt-get install -y ./surfaces/gui/src-tauri/target/release/bundle/deb/OpenWorker_0.2.1_amd64.deb`.
- Installed app launch passed from `/tmp`: `/usr/bin/openworker-desktop`
  spawned `/usr/lib/OpenWorker/sidecar/openworker-server` and the GUI loaded
  `/v1/health`, `/v1/settings`, `/v1/sessions`, `/v1/personas`, and websocket
  endpoints successfully.

## Issues Found

### 1. README test command fails after the documented bootstrap

The README says backend tests can be run with `.venv/bin/pytest`. After running the documented bootstrap (`bash packaging/setup_dev_env.sh`), the full suite failed only in `tests/test_bedrock_provider.py` because `boto3`/`botocore` were not installed.

Repro:

```shell
bash packaging/setup_dev_env.sh
.venv/bin/pytest -q
```

Observed failure class:

```text
ModuleNotFoundError: No module named 'boto3'
ModuleNotFoundError: No module named 'botocore'
```

Workaround verified:

```shell
.venv/bin/pip install -e '.[bedrock]'
.venv/bin/pytest tests/test_bedrock_provider.py -q
.venv/bin/pytest -q
```

PR fix:
- Include the `bedrock` extra in `packaging/setup_dev_env.sh` for developer/test environments.

### 2. Tauri desktop build fails without Linux system packages

`npm run tauri -- build` failed on this Ubuntu 24.04 machine because native Tauri/WebKit dependencies are not installed.

Repro:

```shell
cd surfaces/gui
npm run tauri -- build
```

Observed failure:

```text
The system library `libsoup-3.0` required by crate `soup3-sys` was not found.
The file `libsoup-3.0.pc` needs to be installed and the PKG_CONFIG_PATH environment variable must contain its parent directory.
```

Confirmed missing:

```shell
pkg-config --modversion libsoup-3.0
pkg-config --modversion webkit2gtk-4.1
```

Both commands failed on this machine.

Ubuntu 24.04 package candidates:

```shell
build-essential
cmake
curl
file
libasound2-dev
libsoup-3.0-dev
libwebkit2gtk-4.1-dev
libayatana-appindicator3-dev
libclang-dev
librsvg2-dev
libssl-dev
libxdo-dev
pkg-config
wget
```

PR fixes:
- Add Linux desktop prerequisites to the README.
- Add a Linux packaging/build script alongside `build_dmg.sh` and `build_windows.ps1`.
- Consider adding Linux artifacts to `packaging/make_update_manifest.py` if official Linux desktop releases are intended.

### 3. Linux desktop build needs portable `whisper.cpp` CPU flags

The clean Ubuntu 24.04 VM exposed `avx2` but not `fma` in `/proc/cpuinfo`.
The bundled `whisper.cpp` build enabled an AVX2 path that calls FMA intrinsics,
which failed during `whisper-rs-sys` compilation:

```text
error: inlining failed in call to 'always_inline' '_mm256_fmadd_ps': target specific option mismatch
```

PR fix:
- `packaging/build_linux.sh` defaults the `GGML_*` CPU feature flags to a
  portable distribution build. Builders can still override those environment
  variables for host-optimized local packages.

### 4. Installed Linux `.deb` app must resolve bundled sidecar under `/usr/lib`

The initial local install launched, but when started from the source checkout it
fell back to the repo `.venv` sidecar. Starting from `/tmp` confirmed that the
Linux `.deb` executable lives at `/usr/bin/openworker-desktop` while Tauri
resources are installed under `/usr/lib/OpenWorker/sidecar`.

PR fix:
- Add the Linux `.deb` resource path to the sidecar resolver before the dev
  `.venv` fallback.

## Known Linux Feature Limits From Source Inspection

- Voice Input is explicitly unsupported on Linux in `surfaces/gui/src-tauri/src/lib.rs`.
- Keep-awake is a no-op on Linux in `surfaces/gui/src-tauri/src/lib.rs`.
- Official downloads/update manifest currently cover macOS and Windows, not Linux.
