"""The one-time setup for the full Windows sandbox (design doc, section 7 step 3 and 3d).

`openworker machine sandbox setup` on Windows runs ONE elevated PowerShell script (one UAC
prompt) that:
1. creates TWO hidden local accounts with random passwords nobody sees: `OWSandboxOpenNet`
   (the `open` network profile: no rules at all) and `OWSandboxClosedNet` (the allow-list
   profiles: the network only through our proxy). Choosing a network profile is choosing
   the account; setup never runs per session;
2. hides both from the sign-in screen and denies them remote, network and service logon
   (they are logged on only by `CreateProcessWithLogonW`, an interactive logon);
3. writes one Windows Firewall rule that blocks every OUTBOUND connection for the closed
   account, and, with `windows_wfp.py`, the loopback filters that close every local port
   to it except the proxy's range (Windows Firewall does not look at loopback);
4. stores the passwords in `C:\\ProgramData\\OpenWorker\\sandbox\\account.cred`, readable by
   the user who ran setup, Administrators and SYSTEM, and by nobody else (the sandbox
   accounts cannot read it);
5. makes `C:\\ProgramData\\OpenWorker\\sandbox\\sandboxes`, where each sandbox gets its
   private folder (the user's own temp folder is inside the profile the accounts cannot see);
6. removes what an earlier setup or spike left (`OpenWorkerSandbox`, its rule), and records
   what it changed in `setup.json`, so `remove` can undo exactly that.

The provider reads `account(kind)` at start; without it the sandbox is not usable and
sessions set to use it are refused (`problem()` says why). There is no weaker mode.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from .. import netproxy

OPEN, CLOSED = "open", "closed"
ACCOUNTS = {OPEN: "OWSandboxOpenNet", CLOSED: "OWSandboxClosedNet"}  # 20 characters at most
LEGACY_ACCOUNTS = ("OpenWorkerSandbox", "OwSandbox")  # the first build and the spike
ROOT = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "OpenWorker" / "sandbox"
SANDBOXES = ROOT / "sandboxes"
CRED_FILE = ROOT / "account.cred"
STATE_FILE = ROOT / "setup.json"
FIREWALL_RULE = "OpenWorker sandbox: closed account has no outbound network"  # no comma: the scripts split lists on it
LEGACY_RULES = (
    "OpenWorker sandbox: block outbound",
    "OpenWorker sandbox: no network",
    "OpenWorker sandbox: no other local port",
    # the first two-account build's rule had a comma in its name; the scripts remove it by wildcard
)
SETUP_VERSION = 2

# The elevated script. `$UserSid` is the account that may read the passwords (the person
# running setup); the rest comes from the constants above. `$WfpExe $WfpArg` runs
# windows_wfp.py (the Python here, or the frozen binary's `sandbox-wfp`).
SETUP_SCRIPT = r'''
param([string]$UserSid, [string]$Root, [string]$Rule, [int]$Version, [string]$OpenAccount, [string]$ClosedAccount,
      [string]$Legacy, [string]$LegacyRules, [string]$WfpExe, [string]$WfpArg, [int]$PortLow, [int]$PortHigh)
$ErrorActionPreference = "Stop"
$changed = @()
New-Item -Force -ItemType Directory $Root | Out-Null
$userlist = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList"
New-Item -Force $userlist | Out-Null

# 0. What an earlier setup or the spike left behind.
foreach ($name in ($Legacy -split ",")) {
  if ($name -and (Get-LocalUser -Name $name -ErrorAction SilentlyContinue)) {
    Remove-LocalUser -Name $name
    Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -like "*\$name" } | Remove-CimInstance
    Remove-ItemProperty -Path $userlist -Name $name -ErrorAction SilentlyContinue
    $changed += "removed-legacy-account:$name"
  }
}
foreach ($name in ($LegacyRules -split ";")) {
  if ($name -and (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) { Remove-NetFirewallRule -DisplayName $name; $changed += "removed-legacy-rule" }
}
Get-NetFirewallRule -DisplayName "OpenWorker sandbox: closed account*" -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -ne $Rule } | ForEach-Object { Remove-NetFirewallRule -DisplayName $_.DisplayName; $changed += "removed-legacy-rule" }

# 1. The accounts, each with a password that exists only in this process and in the credential file.
function New-Password {
  $bytes = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return [Convert]::ToBase64String($bytes) + "aA1!"
}
function Ensure-Account([string]$name, [string]$description, [string]$password) {
  $secure = ConvertTo-SecureString $password -AsPlainText -Force
  if (Get-LocalUser -Name $name -ErrorAction SilentlyContinue) {
    Set-LocalUser -Name $name -Password $secure -PasswordNeverExpires $true -UserMayChangePassword $false
  } else {
    New-LocalUser -Name $name -Password $secure -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires -Description $description | Out-Null
    $script:changed += "account:$name"
  }
  if (-not (Get-LocalGroupMember -Group Users -Member $name -ErrorAction SilentlyContinue)) { Add-LocalGroupMember -Group Users -Member $name }
  # 2. Hidden from the sign-in screen.
  New-ItemProperty -Force -Path $userlist -Name $name -Value 0 -PropertyType DWord | Out-Null
  return (Get-LocalUser -Name $name).SID.Value
}
$openPassword = New-Password; $closedPassword = New-Password
$openSid = Ensure-Account $OpenAccount "OpenWorker sandbox account (network open)" $openPassword
$closedSid = Ensure-Account $ClosedAccount "OpenWorker sandbox account (allow list only)" $closedPassword
$changed += "userlist"

# 2. No remote desktop, no network (share) logon, no service logon, for both.
$inf = Join-Path $env:TEMP "ow-rights.inf"; $inf2 = Join-Path $env:TEMP "ow-rights2.inf"; $sdb = Join-Path $env:TEMP "ow-rights.sdb"
secedit /export /cfg $inf /areas USER_RIGHTS | Out-Null
$lines = Get-Content $inf
foreach ($right in @("SeDenyRemoteInteractiveLogonRight", "SeDenyNetworkLogonRight", "SeDenyServiceLogonRight")) {
  foreach ($sid in @($openSid, $closedSid)) {
    if ($lines -match "^$right") { $lines = $lines | ForEach-Object { if ($_ -match "^$right" -and $_ -notmatch [regex]::Escape($sid)) { "$_,*$sid" } else { $_ } } }
    else { $lines = $lines -replace "^\[Privilege Rights\]", "[Privilege Rights]`r`n$right = *$sid" }
  }
}
$lines | Set-Content $inf2 -Encoding Unicode
secedit /configure /db $sdb /cfg $inf2 /areas USER_RIGHTS | Out-Null
Remove-Item -Force $inf, $inf2, $sdb -ErrorAction SilentlyContinue
$changed += "logon-rights"

# 3. The closed account: no outbound network (Windows Firewall), and on loopback only the
#    proxy's ports (WFP filters). The open account gets no rules at all.
Remove-NetFirewallRule -DisplayName $Rule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $Rule -Direction Outbound -Action Block -Profile Any -Enabled True `
  -LocalUser "D:(A;;CC;;;$closedSid)" -Description "OpenWorker: the closed sandbox account reaches the network only through the allow-list proxy on this machine." | Out-Null
$changed += "firewall"
$wfpArgs = @(); if ($WfpArg) { $wfpArgs += $WfpArg }
$wfpOut = & $WfpExe @wfpArgs add --sid $closedSid --port-low $PortLow --port-high $PortHigh 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { throw "the loopback filters could not be written: $wfpOut" }
$wfp = ($wfpOut | ConvertFrom-Json).filters
$changed += "loopback-filters"

# 4. The passwords, readable by the person who ran setup and by administrators only.
$cred = Join-Path $Root "account.cred"
@{ $OpenAccount = $openPassword; $ClosedAccount = $closedPassword } | ConvertTo-Json -Compress | Set-Content -Path $cred -Encoding ASCII -NoNewline
icacls $cred /inheritance:r /grant "*S-1-5-18:F" /grant "*S-1-5-32-544:F" /grant "*${UserSid}:R" | Out-Null
$openPassword = $null; $closedPassword = $null
$changed += "credential"

# 5. Where sandboxes keep their private folders: the person makes them, an account gets each one.
$boxes = Join-Path $Root "sandboxes"
New-Item -Force -ItemType Directory $boxes | Out-Null
icacls $boxes /inheritance:r /grant "*S-1-5-18:(OI)(CI)F" /grant "*S-1-5-32-544:(OI)(CI)F" /grant "*${UserSid}:(OI)(CI)M" /grant "*${openSid}:RX" /grant "*${closedSid}:RX" | Out-Null
$changed += "sandboxes-folder"

# 6. The record.
@{ version = $Version; user_sid = $UserSid; firewall_rule = $Rule; changed = $changed;
   set_up_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ");
   accounts = @{ open = @{ name = $OpenAccount; sid = $openSid }; closed = @{ name = $ClosedAccount; sid = $closedSid } };
   wfp = @{ filters = $wfp; ports = @($PortLow, $PortHigh) } } |
  ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $Root "setup.json") -Encoding ASCII
icacls (Join-Path $Root "setup.json") /grant "*S-1-1-0:R" | Out-Null
Write-Output "ok $openSid $closedSid"
'''

REMOVE_SCRIPT = r'''
param([string]$Root, [string]$Rule, [string]$Accounts, [string]$LegacyRules, [string]$WfpExe, [string]$WfpArg)
$ErrorActionPreference = "Continue"
Remove-NetFirewallRule -DisplayName $Rule -ErrorAction SilentlyContinue
foreach ($name in ($LegacyRules -split ";")) { if ($name) { Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue } }
Get-NetFirewallRule -DisplayName "OpenWorker sandbox: closed account*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
$wfpArgs = @(); if ($WfpArg) { $wfpArgs += $WfpArg }
& $WfpExe @wfpArgs remove 2>&1 | Out-Null
$userlist = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList"
foreach ($name in ($Accounts -split ",")) {
  if (-not $name) { continue }
  Remove-ItemProperty -Path $userlist -Name $name -ErrorAction SilentlyContinue
  if (Get-LocalUser -Name $name -ErrorAction SilentlyContinue) { Remove-LocalUser -Name $name }
  Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -like "*\$name" } | Remove-CimInstance
}
Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue
Write-Output "removed"
'''


def state() -> Optional[dict[str, Any]]:
    """What setup recorded, or None when setup has not run (or was removed)."""
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def current() -> bool:
    """Setup has run and is of this version (an older record means: run it again)."""
    recorded = state()
    return bool(recorded) and int(recorded.get("version") or 0) >= SETUP_VERSION


def account(kind: str = CLOSED) -> Optional[tuple[str, str, str]]:
    """(name, sid, password) of the `open` or `closed` account when the full mode can be
    used by THIS user: setup of this version has run and the credential file is readable.
    The password never leaves the process."""
    recorded = state()
    if not recorded or int(recorded.get("version") or 0) < SETUP_VERSION:
        return None
    entry = (recorded.get("accounts") or {}).get(kind) or {}
    name, sid = str(entry.get("name") or ""), str(entry.get("sid") or "")
    if not name or not sid:
        return None
    try:
        passwords = json.loads(CRED_FILE.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None
    password = str(passwords.get(name) or "")
    return (name, sid, password) if password else None


def problem() -> Optional[str]:
    """Why the Windows sandbox cannot be used by this user right now, or None."""
    recorded = state()
    if not recorded:
        return "the one-time setup has not run on this PC (`openworker machine sandbox setup`)"
    if not current():
        return "an older setup is recorded on this PC; run `openworker machine sandbox setup` again"
    for kind in (OPEN, CLOSED):
        if account(kind) is None:
            return f"the sandbox account for the {kind} network mode is not usable by this user; run `openworker machine sandbox setup` again"
    return None


def filters_recorded() -> bool:
    recorded = state() or {}
    return bool((recorded.get("wfp") or {}).get("filters"))


def reap_private_folders() -> list[str]:
    """Remove the private folders of sandboxes whose daemon is gone: a server killed hard
    never ran destroy(), and its `owr-*` folder under SANDBOXES stayed behind. A folder
    is alive while its named pipe exists. Best effort; returns what was removed."""
    import shutil

    from ..runner import winpipe

    removed: list[str] = []
    try:
        names = os.listdir(SANDBOXES)
    except OSError:
        return removed
    for name in names:
        folder = SANDBOXES / name
        if not name.startswith("owr-") or not folder.is_dir():
            continue
        if os.path.exists(winpipe.pipe_name(name)):
            continue  # its daemon still listens
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            removed.append(str(folder))
    return removed


def _powershell(script: str, arguments: list[str], *, elevate: bool) -> subprocess.CompletedProcess:
    """Run a script file with PowerShell; `elevate` asks for administrator rights (the UAC
    prompt) and waits. Output comes back through a file, because an elevated process has
    no pipes to us."""
    folder = tempfile.mkdtemp(prefix="ow-setup-")
    path = os.path.join(folder, "setup.ps1")
    out = os.path.join(folder, "out.txt")
    Path(path).write_text(script, encoding="utf-8-sig")
    inner = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, *arguments]
    try:
        if not elevate:
            return subprocess.run(["powershell.exe", *inner], capture_output=True, text=True, timeout=600)
        quoted = " ".join(f"'{a}'" for a in inner)
        starter = (
            f"$p = Start-Process -FilePath powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
            f"-ArgumentList @({quoted}, '*>', '{out}'); exit $p.ExitCode"
        )
        done = subprocess.run(["powershell.exe", "-NoProfile", "-Command", starter], capture_output=True, text=True, timeout=900)
        said = Path(out).read_text(encoding="utf-8", errors="replace") if os.path.exists(out) else ""
        return subprocess.CompletedProcess(done.args, done.returncode, said, done.stderr)
    finally:
        import shutil

        shutil.rmtree(folder, ignore_errors=True)


def is_elevated() -> bool:
    import ctypes

    try:
        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except OSError:
        return False


_ADMINISTRATORS_SID = "S-1-5-32-544"


def can_elevate() -> bool:
    """This user can answer the administrator prompt: already elevated, or a member of
    Administrators (under UAC the group is on the filtered token, marked deny-only, and
    `whoami` still lists it). A standard user gets a prompt asking for someone else's
    password, which the app must not show as "one prompt"."""
    if sys.platform != "win32":
        return False
    if is_elevated():
        return True
    try:
        done = subprocess.run(["whoami", "/groups", "/fo", "csv"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return _ADMINISTRATORS_SID in (done.stdout or "")


NOT_SET_UP, OLDER, BROKEN, READY = "not_set_up", "older", "broken", "ready"
SETUP_COMMAND = "openworker machine sandbox setup"


def info() -> dict[str, Any]:
    """What Settings ▸ Sandbox shows about the setup on this PC: its state, when it ran,
    whether this user can run it, and the command to hand to an administrator."""
    recorded = state()
    if not recorded:
        status = NOT_SET_UP
    elif not current():
        status = OLDER
    elif problem():
        status = BROKEN
    else:
        status = READY
    return {
        "state": status,
        "set_up_at": str(recorded.get("set_up_at") or "") if recorded else "",
        "problem": problem() or "",
        "can_elevate": can_elevate(),
        "command": SETUP_COMMAND,
    }


def _wfp_command() -> tuple[str, str]:
    """How the elevated script runs windows_wfp.py: (executable, first argument)."""
    from ..launch import frozen

    if frozen():
        return sys.executable, "sandbox-wfp"
    from .. import windows_wfp

    return sys.executable, str(Path(windows_wfp.__file__).resolve())


def _wfp_arguments() -> list[str]:
    exe, arg = _wfp_command()
    return ["-WfpExe", exe, "-WfpArg", arg]


def run_setup() -> tuple[bool, str]:
    """Create the accounts and everything around them. Returns (ok, what the script said)."""
    from .. import winsec

    ports = netproxy.WINDOWS_PORTS
    arguments = [
        "-UserSid", winsec.current_user_sid(), "-Root", str(ROOT), "-Rule", FIREWALL_RULE, "-Version", str(SETUP_VERSION),
        "-OpenAccount", ACCOUNTS[OPEN], "-ClosedAccount", ACCOUNTS[CLOSED],
        "-Legacy", ",".join(LEGACY_ACCOUNTS), "-LegacyRules", ";".join(LEGACY_RULES),
        *_wfp_arguments(), "-PortLow", str(ports.start), "-PortHigh", str(ports.stop - 1),
    ]  # fmt: skip
    done = _powershell(SETUP_SCRIPT, arguments, elevate=not is_elevated())
    said = (done.stdout or "").strip() + (("\n" + done.stderr.strip()) if done.stderr and done.stderr.strip() else "")
    return done.returncode == 0 and "ok " in said, said


def run_remove() -> tuple[bool, str]:
    names = ",".join([*ACCOUNTS.values(), *LEGACY_ACCOUNTS])
    arguments = ["-Root", str(ROOT), "-Rule", FIREWALL_RULE, "-Accounts", names, "-LegacyRules", ";".join(LEGACY_RULES), *_wfp_arguments()]
    done = _powershell(REMOVE_SCRIPT, arguments, elevate=not is_elevated())
    said = (done.stdout or "").strip()
    return done.returncode == 0 and "removed" in said, said


def _firewall_rule_enabled() -> bool:
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", f"(Get-NetFirewallRule -DisplayName '{FIREWALL_RULE}' -ErrorAction SilentlyContinue).Enabled"],
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    return "True" in (done.stdout or "")


def checks() -> list[tuple[str, bool, str]]:
    """(what, ok, detail) rows for `sandbox status` on Windows."""
    if sys.platform != "win32":
        return []
    recorded = state()
    rows: list[tuple[str, bool, str]] = []
    if recorded and not current():
        rows.append(("the sandbox accounts exist (setup has run)", False, "an older setup is recorded; run `openworker machine sandbox setup` again"))
        return rows
    rows.append(("the sandbox accounts exist (setup has run)", bool(recorded), "" if recorded else "run `openworker machine sandbox setup`"))
    if not recorded:
        return rows
    usable = account(OPEN) is not None and account(CLOSED) is not None
    rows.append(("this user can start sandboxes as those accounts", usable, "" if usable else f"{CRED_FILE} is not readable by this user; run setup as this user"))
    rule = _firewall_rule_enabled()
    rows.append(("the closed account's outbound traffic is blocked (firewall rule)", rule, "" if rule else "the rule is missing; run setup again"))
    from .. import windows_wfp

    present = windows_wfp.present()
    if present is None:
        rows.append(("the closed account's loopback filters exist", filters_recorded(), "recorded by setup; proved from inside at every session start"))
    else:
        rows.append(("the closed account's loopback filters exist", present, "" if present else "the filters are missing; run setup again"))
    return rows
