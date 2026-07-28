"""Packaging regression tests for Markdown-backed built-in personas.

The production PyInstaller spec is executable Python, but PyInstaller is a
build-only dependency and is intentionally absent from the normal test
environment. Execute the spec with small stand-ins here to validate the data
collection contract without producing a sidecar binary.
"""

from __future__ import annotations

import os
import shutil
import sys
import types
from pathlib import Path

from coworker.personas.registry import PersonaRegistry


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "packaging" / "openworker-server.spec"
BUILTIN_DIR = ROOT / "coworker" / "personas" / "builtin"
PACKAGE_DESTINATION = Path("coworker") / "personas" / "builtin"


def _execute_spec(monkeypatch):
    """Run the spec with a data collector matching PyInstaller's layout rule."""

    collected_calls: list[tuple[str, tuple[str, ...]]] = []

    def collect_submodules(_package):
        return []

    def collect_all(_package):
        return [], [], []

    def collect_data_files(package, *, includes=None, **_kwargs):
        # PyInstaller emits each data file under its path relative to the
        # package base. For ``coworker`` and this include, that is exactly
        # ``coworker/personas/builtin`` in the frozen sidecar's _internal tree.
        collected_calls.append((package, tuple(includes or ())))
        package_dir = ROOT / package.replace(".", os.sep)
        sources = [
            source
            for pattern in includes or ("**/*",)
            for source in sorted(package_dir.glob(pattern))
            if source.is_file()
        ]
        return [
            (str(source), str(source.parent.relative_to(ROOT))) for source in sources
        ]

    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_all = collect_all
    hooks.collect_data_files = collect_data_files
    hooks.collect_submodules = collect_submodules
    utils = types.ModuleType("PyInstaller.utils")
    utils.hooks = hooks
    pyinstaller = types.ModuleType("PyInstaller")
    pyinstaller.utils = utils
    monkeypatch.setitem(sys.modules, "PyInstaller", pyinstaller)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils", utils)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)

    class Analysis:
        def __init__(self, _scripts, **kwargs):
            self.datas = kwargs["datas"]
            self.binaries = kwargs["binaries"]
            self.pure = []
            self.scripts = []

    class PYZ:
        def __init__(self, *_args, **_kwargs):
            pass

    class EXE:
        def __init__(self, *_args, **_kwargs):
            pass

    class COLLECT:
        def __init__(self, *_args, **_kwargs):
            pass

    namespace = {
        "__file__": str(SPEC_PATH),
        "__name__": "openworker_server_spec_test",
        "SPECPATH": str(SPEC_PATH.parent),
        "Analysis": Analysis,
        "PYZ": PYZ,
        "EXE": EXE,
        "COLLECT": COLLECT,
    }
    exec(compile(SPEC_PATH.read_text(encoding="utf-8"), str(SPEC_PATH), "exec"), namespace)
    return collected_calls, namespace["a"].datas


def test_spec_stages_all_builtin_personas_at_registry_lookup_path(monkeypatch, tmp_path):
    calls, datas = _execute_spec(monkeypatch)

    assert calls == [("coworker", ("personas/builtin/*.md",))]

    expected_sources = sorted(BUILTIN_DIR.glob("*.md"))
    expected_datas = {
        (str(source), str(PACKAGE_DESTINATION)) for source in expected_sources
    }
    assert set(datas) == expected_datas
    assert {source.name for source in expected_sources} >= {"ops.md", "korean-docs.md"}

    # Mirror the relevant frozen layout: PyInstaller stages package data below
    # _internal, then PersonaRegistry reads builtin manifests beside its module.
    staged_builtin = tmp_path / "_internal" / PACKAGE_DESTINATION
    staged_builtin.mkdir(parents=True)
    for source, destination in datas:
        assert Path(destination) == PACKAGE_DESTINATION
        shutil.copy2(source, staged_builtin / Path(source).name)

    registry = PersonaRegistry(builtin_dir=staged_builtin)
    korean_docs = registry.get("korean-docs")
    assert korean_docs is not None
    assert korean_docs.manifest is not None
    assert korean_docs.manifest.name == "Korean Document Coworker"
