# Contributing to OpenWorker

Thanks for helping improve OpenWorker. Start with the [README](README.md) for the
product overview and local development setup. This guide covers the contribution
workflow.

## Pick a change

Search [open issues](https://github.com/andrewyng/openworker/issues) and
[open pull requests](https://github.com/andrewyng/openworker/pulls) before
starting. Check the issue discussion and the PR list for the issue number or
relevant code path so you do not duplicate active work. For a new feature or a
large behavior change, discuss the direction in an issue first: the project has
an active internal roadmap. For security vulnerabilities, follow
[SECURITY.md](SECURITY.md) and report privately.

Prefer a focused PR with a reproduction, the smallest useful fix, and a
regression test. Keep unrelated cleanup on a separate branch.

## Prepare your fork

Python 3.10 or newer and Node.js 20 are required by the project metadata and
CI. Check `python3 --version` before running the bootstrap script: it currently
uses that exact executable, and some macOS installations still provide Python
3.9 as `python3`. For desktop development, install the platform prerequisites
for Tauri 2; the browser UI and Python tests do not need a packaged desktop app.

Fork the repository on GitHub, then run:

```bash
git clone https://github.com/<your-user>/openworker.git
cd openworker
git remote add upstream https://github.com/andrewyng/openworker.git
git fetch upstream
git switch -c fix/short-description upstream/main
bash packaging/setup_dev_env.sh
```

On Windows, use WSL for this bootstrap flow. Git Bash currently creates a
`.venv/Scripts/` layout that the script does not handle (see
[#9](https://github.com/andrewyng/openworker/issues/9)). The README shows how
to start the server and browser UI; from `surfaces/gui/`, run `npm ci` before
GUI tests or development.

## Validate the change

Run the narrow checks while iterating, then the relevant full checks before
opening a PR:

| Changed area | Focused check | Full check |
| --- | --- | --- |
| Python backend | `.venv/bin/pytest tests/test_relevant_area.py -q` | `.venv/bin/pytest tests -q --cov=coworker --cov-report=term-missing` |
| GUI logic | `cd surfaces/gui && npm test -- path/to/relevant.test.tsx` | `cd surfaces/gui && npx tsc --noEmit && npm test` |
| GUI workflow | `cd surfaces/gui && npx playwright test e2e/relevant.spec.ts` | `cd surfaces/gui && npx playwright install chromium && npm run e2e` |

CI uses Python 3.12, Node 20, and the hermetic Playwright suite; it installs
`.[messaging,dev,bedrock]` for the Python job. In a manually created Windows
virtual environment, run Python tests with `.venv/Scripts/python.exe -m pytest`.
If you cannot run a check locally, say so in the PR and include the checks you
did run.

## Open a pull request

1. Rebase or merge the latest `upstream/main` into your branch and resolve any
   conflicts.
2. Commit the focused change and push your branch to your fork.
3. Open a PR targeting `andrewyng/openworker:main`. Link the issue and explain
   the failure, fix, and validation.
4. Attach before/after screenshots for visible UI bugs and changes, as requested
   in the README. For nonvisual changes, include a short reproduction and test
   output, and explain why a screenshot is not applicable.
5. Watch CI and review feedback; update the same branch with corrections.

A first-time fork's GitHub Actions run may require maintainer approval. Do not
interpret an unstarted workflow as a passing test.
