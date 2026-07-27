"""The [messaging] extra must survive into release builds.

`openworker-server.spec` collects slack_bolt/telegram only when it can IMPORT them, inside a
`try/except: pass`. So a build venv without the extra produces no error and no bundled
library — it just ships a sidecar whose Telegram / Slack Socket Mode listeners hit their
ImportError guard and return False forever (the silent-connector half of #257).

Nothing else catches that: the packaged app is built by a separate workflow, and a missing
optional import is invisible until a user connects a bot and receives nothing. These pin the
three files that have to agree.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "openworker-server.spec"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

# messaging dependency (as named on PyPI) -> the module the spec has to be able to import.
MESSAGING_IMPORTS = {
    "python-telegram-bot": "telegram",
    "slack-bolt": "slack_bolt",
}


def _messaging_extra() -> list[str]:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    return pyproject["project"]["optional-dependencies"]["messaging"]


def _sidecar_venv_run() -> str:
    """The release job step that provisions the .venv the build scripts freeze from."""
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text())
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            if "venv" in (step.get("name") or "").lower():
                return step.get("run") or ""
    pytest.fail("no sidecar venv step found in release.yml")


def test_messaging_extra_still_declares_the_inbound_listeners():
    declared = " ".join(_messaging_extra())
    for dist in MESSAGING_IMPORTS:
        assert dist in declared, f"{dist} dropped from the [messaging] extra"


def test_spec_collects_every_messaging_import():
    spec = SPEC.read_text()
    for dist, module in MESSAGING_IMPORTS.items():
        assert module in spec, (
            f"{module} ({dist}) is not collected by openworker-server.spec, so it will not "
            "be bundled even when the build venv has it"
        )


def test_release_build_installs_the_messaging_extra():
    """Without this the spec's `try: collect_submodules(...) except: pass` silently no-ops."""
    run = _sidecar_venv_run()
    assert "[messaging]" in run, (
        "the release sidecar venv installs the package without the [messaging] extra — "
        "telegram/slack_bolt would not be importable at freeze time and never get bundled"
    )


def test_release_build_fails_fast_when_messaging_is_missing():
    """A bundling regression must break the build, not ship a mute connector."""
    run = _sidecar_venv_run()
    imports = [m for m in MESSAGING_IMPORTS.values() if m in run]
    assert imports, (
        "release.yml never imports the messaging modules after install; add an import check "
        "so a missing extra fails the build instead of shipping a sidecar that can't listen"
    )
