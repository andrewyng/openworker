"""Exercise setup's PowerShell launcher without changing Windows accounts or rules."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from coworker.sandbox.providers import windows_setup


@pytest.fixture
def powershell_launcher(monkeypatch, tmp_path):
    """Run real PowerShell, replacing only the request for an elevated process."""
    shell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("requires PowerShell")
    shell_literal = shell.replace("'", "''")
    start_process = rf"""
function Start-Process {{
    param([string]$FilePath, [string]$Verb, [switch]$Wait,
          [switch]$PassThru, [string]$WindowStyle, [string[]]$ArgumentList)
    Microsoft.PowerShell.Management\Start-Process -FilePath '{shell_literal}' `
        -ArgumentList $ArgumentList -Wait -PassThru
}}
"""
    run = subprocess.run

    def launch(command, **kwargs):
        assert command[0] == "powershell.exe"
        if "-Command" in command:
            command = [shell, "-NoProfile", "-Command", start_process + command[-1]]
        else:
            command = [shell, *command[1:]]
        return run(command, **kwargs)

    monkeypatch.setattr(windows_setup.subprocess, "run", launch)
    parent = tmp_path / "Sam's files [1] $data"
    parent.mkdir()
    mkdtemp = windows_setup.tempfile.mkdtemp
    monkeypatch.setattr(windows_setup.tempfile, "mkdtemp", lambda **kwargs: mkdtemp(dir=parent, **kwargs))
    return parent


@pytest.mark.parametrize("elevate", [False, True])
def test_powershell_preserves_arguments_and_captures_output(powershell_launcher, elevate):
    """Spaces and PowerShell metacharacters must reach the script as literal data."""
    rule = windows_setup.FIREWALL_RULE
    value = "C:\\Sam's files\\工具 [1] $name `tick; & \"quote\""
    if not elevate:
        value = "C:\\Program Files\\OpenWorker"
    result = windows_setup._powershell(
        "param([string]$Rule, [string]$Value)\n"
        "@{Rule=$Rule; Value=$Value; Extra=@($args)} | ConvertTo-Json -Compress",
        ["-Rule", rule, "-Value", value],
        elevate=elevate,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout) == {"Rule": rule, "Value": value, "Extra": []}
    assert not list(powershell_launcher.iterdir())


@pytest.mark.parametrize(
    ("script", "status", "message"),
    [
        ("'before failure'; throw 'setup failed'", 1, "setup failed"),
        ("'before failure'; exit 7", 7, "before failure"),
    ],
)
def test_elevated_powershell_preserves_failure_output_and_status(powershell_launcher, script, status, message):
    """Both terminating errors and explicit exits must retain their diagnostic output."""
    result = windows_setup._powershell(script, [], elevate=True)
    assert result.returncode == status, (result.stdout, result.stderr)
    assert "before failure" in result.stdout
    assert message in result.stdout
    assert not list(powershell_launcher.iterdir())


@pytest.mark.parametrize(("operation", "marker"), [("setup", "ok S-1 S-2"), ("remove", "removed")])
def test_setup_callers_read_the_elevated_success_marker(powershell_launcher, monkeypatch, operation, marker):
    """Both callers must receive their success marker from the elevated script."""
    from coworker.sandbox import winsec

    monkeypatch.setattr(windows_setup, "SETUP_SCRIPT", "Write-Output 'ok S-1 S-2'")
    monkeypatch.setattr(windows_setup, "REMOVE_SCRIPT", "Write-Output 'removed'")
    monkeypatch.setattr(windows_setup, "is_elevated", lambda: False)
    monkeypatch.setattr(windows_setup, "_wfp_command", lambda: ("python.exe", "wfp.py"))
    monkeypatch.setattr(winsec, "current_user_sid", lambda: "S-1")
    assert getattr(windows_setup, f"run_{operation}")() == (True, marker)
    assert not list(powershell_launcher.iterdir())


@pytest.mark.parametrize("elevate", [False, True])
def test_powershell_normal_completion_ignores_native_exit_status(powershell_launcher, elevate):
    """A handled native failure must not replace the script's normal success status."""
    result = windows_setup._powershell(
        "& (Get-Process -Id $PID).Path -NoProfile -Command 'exit 7'; 'removed'",
        [],
        elevate=elevate,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout.strip() == "removed"
    assert not list(powershell_launcher.iterdir())


@pytest.mark.parametrize("elevate", [False, True])
def test_powershell_preserves_script_scope(powershell_launcher, elevate):
    """Setup's functions must update the script's own change record."""
    result = windows_setup._powershell(
        "$changed = @(); function Mark { $script:changed += 'account' }; "
        "Mark; $changed += 'done'; ConvertTo-Json -InputObject $changed -Compress",
        [],
        elevate=elevate,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout) == ["account", "done"]
    assert not list(powershell_launcher.iterdir())
