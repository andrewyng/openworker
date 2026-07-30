"""Contract tests for the pinned, read-only local Kordoc runtime."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from coworker.integrations.kordoc import (
    KORDOC_MCP_TOOL_ALLOWLIST,
    KORDOC_VERSION,
    detect_kordoc_runtime,
    normalize_kordoc_metadata_format,
)


def _touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _installation(tmp_path: Path, *, version: str = KORDOC_VERSION) -> tuple[Path, Path]:
    node = _touch(tmp_path / "node" / "node.exe")
    _touch(tmp_path / "node" / "node_modules" / "npm" / "bin" / "npm-cli.js")
    root = tmp_path / "global-modules"
    package = root / "kordoc"
    _touch(package / "package.json", json.dumps({"version": version}))
    _touch(package / "dist" / "cli.js")
    _touch(package / "dist" / "mcp.js")
    return node, root


def _npm_root_runner(root: Path, seen: list[tuple[list[str], dict]]):
    def runner(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=str(root) + "\n", stderr="")

    return runner


def test_allowlist_is_the_exact_read_only_parse_surface():
    assert KORDOC_MCP_TOOL_ALLOWLIST == [
        "detect_format",
        "parse_metadata",
        "parse_chunks",
        "parse_pages",
        "parse_table",
    ]


def test_metadata_format_normalizer_uses_detector_and_preserves_bad_results():
    upstream = json.dumps(
        {"format": "hwpx", "title": "Synthetic DOCX"}, ensure_ascii=False, indent=2
    )

    normalized = normalize_kordoc_metadata_format(
        upstream, r"C:\workspace\synthetic.docx: docx"
    )

    assert json.loads(normalized) == {
        "format": "docx",
        "title": "Synthetic DOCX",
    }
    assert json.loads(
        normalize_kordoc_metadata_format(upstream, "source.xlsx: xlsx")
    )["format"] == "xlsx"
    unrelated = json.dumps({"format": "pdf", "title": "PDF"})
    assert (
        normalize_kordoc_metadata_format(unrelated, "source.docx: docx")
        == unrelated
    )
    assert (
        normalize_kordoc_metadata_format(upstream, "source.hwp: hwp") == upstream
    )
    assert normalize_kordoc_metadata_format(upstream, "malformed") == upstream
    error = {"error": "metadata failed"}
    assert normalize_kordoc_metadata_format(error, "source.docx: docx") is error


def test_detects_exact_global_package_and_uses_non_shell_npm_lookup(tmp_path):
    node, root = _installation(tmp_path)
    calls: list[tuple[list[str], dict]] = []

    status = detect_kordoc_runtime(
        node_executable=node, runner=_npm_root_runner(root, calls)
    )

    assert status.ready and status.state == "ready"
    assert status.runtime is not None
    assert status.runtime.node_executable == node.resolve()
    assert status.runtime.mcp_path == (root / "kordoc" / "dist" / "mcp.js").resolve()
    assert status.public() == {"state": "ready", "version": KORDOC_VERSION}
    assert calls[0][0][0] == str(node.resolve())
    assert Path(calls[0][0][1]).name == "npm-cli.js"
    assert calls[0][0][2:] == ["root", "-g"]
    assert calls[0][1]["shell"] is False


@pytest.mark.parametrize(
    "prepare, expected_state, needle",
    [
        (
            lambda root: (root / "kordoc").rename(root / "missing-kordoc"),
            "not_installed",
            "not installed",
        ),
        (
            lambda root: (root / "kordoc" / "package.json").write_text(
                json.dumps({"version": "4.2.4"}), encoding="utf-8"
            ),
            "incompatible",
            "exact version 4.2.3",
        ),
        (
            lambda root: (root / "kordoc" / "dist" / "mcp.js").unlink(),
            "incompatible",
            "incomplete",
        ),
    ],
)
def test_discovery_reports_missing_or_incompatible_runtime_without_path_leaks(
    tmp_path, prepare, expected_state, needle
):
    node, root = _installation(tmp_path)
    prepare(root)

    status = detect_kordoc_runtime(
        node_executable=node, runner=_npm_root_runner(root, [])
    )

    assert status.state == expected_state and status.ready is False
    assert needle in (status.error or "").lower()
    assert str(tmp_path) not in json.dumps(status.public())
