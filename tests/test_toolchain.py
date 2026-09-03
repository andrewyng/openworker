"""Tool resolution + pinned managed installs (OPE-84).

The bug this guards against: a Finder-launched app gets launchd's minimal PATH, so every
brew/nvm-installed scanner is invisible and a security review silently loses checks.
"""

from __future__ import annotations

import hashlib
import os
import stat

import pytest

from coworker import toolchain


def _make_exe(path, body: str = "#!/bin/sh\necho hi\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_resolve_prefers_path(tmp_path, monkeypatch):
    on_path = _make_exe(tmp_path / "bin" / "semgrep")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert toolchain.resolve("semgrep") == str(on_path.resolve())


def test_resolve_finds_tools_launchd_path_cannot_see(tmp_path, monkeypatch):
    """The actual production failure: PATH is bare, the tool is in a brew-style dir."""
    brew = tmp_path / "opt" / "homebrew" / "bin"
    gitleaks = _make_exe(brew / "gitleaks")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # what a Finder launch really gets
    monkeypatch.setattr(toolchain, "_KNOWN_DIRS", (str(brew),))
    assert toolchain.resolve("gitleaks") == str(gitleaks.resolve())


def test_resolve_returns_absolute_path(tmp_path, monkeypatch):
    """Callers must be able to invoke without depending on PATH at all."""
    _make_exe(tmp_path / "bin" / "trivy")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert os.path.isabs(toolchain.resolve("trivy") or "")


def test_missing_reports_only_absent_tools(tmp_path, monkeypatch):
    _make_exe(tmp_path / "bin" / "gitleaks")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(toolchain, "_KNOWN_DIRS", ())
    monkeypatch.setattr(toolchain, "MANAGED", {})
    assert toolchain.missing(["gitleaks", "semgrep"]) == ["semgrep"]


def test_unknown_tool_resolves_to_none(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(toolchain, "_KNOWN_DIRS", ())
    assert toolchain.resolve("definitely-not-a-real-tool") is None


def test_registry_entries_are_pinned_and_digested():
    """Every managed download must carry a version and a full SHA-256 — an unpinned
    entry would mean 'download whatever is current', which is the thing we refuse."""
    assert toolchain.MANAGED, "registry should not be empty"
    for name, tool in toolchain.MANAGED.items():
        assert tool.version and tool.version[0].isdigit(), name
        assert tool.summary, f"{name} needs a summary for the consent card"
        for key, dl in tool.downloads.items():
            assert len(dl.sha256) == 64, f"{name}/{key} digest looks wrong"
            assert int(dl.sha256, 16) >= 0  # hex
            assert tool.version in dl.url, f"{name}/{key} url must pin the version"
            assert dl.url.startswith("https://"), f"{name}/{key} must be https"


def test_trivy_is_pinned_for_every_platform_we_ship():
    """tfsec is deprecated upstream (folded into trivy); `trivy config` is the IaC scanner
    the cloud-posture bundle drives, so its pin must exist wherever the app runs."""
    tool = toolchain.MANAGED["trivy"]
    assert set(tool.downloads) >= {"darwin_arm64", "darwin_amd64", "linux_amd64"}
    for dl in tool.downloads.values():
        assert dl.member == "trivy"  # release assets are tarballs, not bare binaries
    assert "tfsec" not in toolchain.MANAGED


def test_describe_surfaces_what_the_user_is_approving(monkeypatch):
    monkeypatch.setattr(toolchain, "_platform_key", lambda: "darwin_arm64")
    info = toolchain.describe("gitleaks")
    assert info and info["version"] and info["sha256"] and info["url"]
    assert "secret" in info["summary"].lower()
    assert info["source"] == "github.com/gitleaks"  # publisher, human-readable


def test_install_refuses_a_tampered_download(tmp_path, monkeypatch):
    """The whole point of pinning: a mismatched artifact never lands on disk."""
    monkeypatch.setattr(toolchain, "managed_dir", lambda: tmp_path / "tools")
    monkeypatch.setattr(toolchain, "_platform_key", lambda: "darwin_arm64")

    class FakeResp:
        def read(self):
            return b"malicious payload"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(toolchain.urllib.request, "urlopen", lambda *a, **k: FakeResp())

    with pytest.raises(ValueError, match="checksum mismatch"):
        toolchain.install("gitleaks")
    assert not (tmp_path / "tools").exists() or not list(
        (tmp_path / "tools").rglob("gitleaks")
    )


def test_install_writes_a_verified_binary(tmp_path, monkeypatch):
    payload = b"#!/bin/sh\necho scanned\n"
    digest = hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(toolchain, "managed_dir", lambda: tmp_path / "tools")
    monkeypatch.setattr(toolchain, "_platform_key", lambda: "darwin_arm64")
    monkeypatch.setattr(
        toolchain,
        "MANAGED",
        {
            "osv-scanner": toolchain.ManagedTool(
                name="osv-scanner",
                version="2.5.0",
                summary="checks lockfiles",
                downloads={
                    "darwin_arm64": toolchain.Download(
                        url="https://example.invalid/osv-scanner", sha256=digest
                    )
                },
            )
        },
    )

    class FakeResp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(toolchain.urllib.request, "urlopen", lambda *a, **k: FakeResp())

    path = toolchain.install("osv-scanner")
    assert os.access(path, os.X_OK)
    assert open(path, "rb").read() == payload
    # Installed tools are resolvable afterwards, even with an empty PATH.
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(toolchain, "_KNOWN_DIRS", ())
    assert toolchain.resolve("osv-scanner") == path
    # And linked under the stable bin dir, so a shell with that dir on PATH picks the
    # tool up by name the moment the install finishes — no respawn, no full paths.
    linked = toolchain.bin_dir() / "osv-scanner"
    assert linked.exists() and open(linked, "rb").read() == payload
