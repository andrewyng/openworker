# Contributing to OpenWorker

Thank you for helping improve OpenWorker. Small, focused changes with clear
evidence are the easiest to review while the project is moving quickly.

## Before opening a pull request

- Search existing issues and pull requests for overlapping work.
- For a new feature or broad architecture change, open an issue first so the
  direction can be aligned with the maintainers' active roadmap.
- Keep unrelated cleanup out of the same pull request.
- Do not include API keys, connector tokens, user data, generated credentials,
  or private transcripts in commits, logs, screenshots, or fixtures.
- Report suspected vulnerabilities through [SECURITY.md](SECURITY.md), not a
  public issue.

## Development setup

Prerequisites are Python 3.10+, Node.js 20+, and Rust via `rustup` for desktop
work. From the repository root:

```shell
bash packaging/setup_dev_env.sh
```

For the same frozen Python dependency graph used by CI, install the pinned
resolver and sync from the committed universal lockfile:

```shell
python -m pip install "uv==0.11.32"
uv sync --frozen --extra messaging --extra dev
```

Start the Python server with:

```shell
.venv/bin/openworker-server --cwd ~/some/project --port 8765
```

On Windows, use `.venv\Scripts\openworker-server.exe`. Start the browser UI
from `surfaces/gui` with `npm install` and `npm run dev`, or use
`npm run tauri dev` for the full desktop shell.

## Verification

Run the checks relevant to the files you changed:

```shell
# Python backend
.venv/bin/pytest

# React UI
cd surfaces/gui
npm ci
npm test
npm run build

# Hermetic browser tests
npm run e2e

# Rust components
cargo test --manifest-path surfaces/gui/src-tauri/Cargo.toml
cargo test --manifest-path stt/Cargo.toml
```

If a platform-specific installer or desktop path changed, also run the matching
packaging script described in the README and state which platforms were not
tested.

## Pull request checklist

- Explain the user-visible problem and why the chosen scope is appropriate.
- Describe security, privacy, compatibility, and migration implications.
- Add or update tests for behavior changes.
- Attach before/after screenshots for UI changes, as requested in the README.
- List the exact commands run and their results.
- Call out residual risks, follow-up work, and checks you could not run.
- Keep dependency lockfiles in sync with their manifests.

By contributing, you agree that your contribution is licensed under the
repository's MIT License.
