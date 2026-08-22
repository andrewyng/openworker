#requires -Version 5.1
<#
.SYNOPSIS
  Build the Coworker Windows desktop app + NSIS (.exe) and MSI installers.

.DESCRIPTION
  The Windows counterpart to build_dmg.sh:
    1. PyInstaller-bundle the server into a standalone onedir folder (no venv at runtime).
    2. Stage it at binaries\sidecar\ for Tauri's `resources` slot.
    3. Authenticode-sign the staged sidecar exe when signing credentials are present
       (Tauri's bundler only signs the artifacts it produces — resources ship as-is,
       and Smart App Control evaluates every PE that runs, not just the installer).
    4. `tauri build --bundles nsis,msi` -> Coworker NSIS setup .exe + .msi (resources copied in).

  Prerequisites (see the toolchain notes in the PR/plan):
    - Rust (rustup) with the x86_64-pc-windows-msvc target + the MSVC C++ build tools (link.exe).
    - Node + npm (frontend build).
    - A Python venv at platform\.venv with this package installed editable, plus pyinstaller.
      `typer` is needed only at build time: PyInstaller walks the `mcp` package and `mcp.cli`
      calls sys.exit() at import if typer is absent, which aborts the freeze.
        py -m venv .venv ; .\.venv\Scripts\pip install -e ".[bedrock]" pyinstaller tzdata typer

  Authenticode signing mirrors the macOS pattern in release.yml: it activates when signing
  credentials are present in the environment and the build degrades to UNSIGNED when they
  are absent (first launch then shows a SmartScreen warning; machines with Smart App
  Control on block the app outright). Three credential shapes, first match wins — see
  docs/windows-signing.md for procurement and details:
    WINDOWS_SIGN_COMMAND               full custom command template, %1 = file to sign
                                       (cloud signing CLIs: Trusted Signing et al.)
    WINDOWS_CERTIFICATE_THUMBPRINT     signtool against a cert already in the store
                                       (hardware token / pre-provisioned machine)
    WINDOWS_CERTIFICATE (+ _PASSWORD)  base64 PFX, imported for this build
    WINDOWS_TIMESTAMP_URL              RFC 3161 timestamp server override (default DigiCert)

  Experimental (use-at-your-own-risk) connectors are EXCLUDED from this build by default —
  the spec strips coworker.connectors.experimental. Self-builders can opt in with:
    $env:COWORKER_EXPERIMENTAL = "1"; .\build_windows.ps1
#>
[CmdletBinding()]
param(
    # Which installer bundles to produce. Both by default.
    [string]$Bundles = "nsis,msi"
)
$ErrorActionPreference = "Stop"

$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Platform = Split-Path -Parent $Here
$Gui      = Join-Path $Platform "surfaces\gui"
$Venv     = Join-Path $Platform ".venv"
$PyInst   = Join-Path $Venv "Scripts\pyinstaller.exe"

function Require-Cmd($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$name' not found on PATH. See the prerequisites in this script's header."
    }
}

Require-Cmd rustc
Require-Cmd npm
if (-not (Test-Path $PyInst)) {
    throw "PyInstaller not found at $PyInst. Create the venv and install deps (see header)."
}

# Host target triple, e.g. x86_64-pc-windows-msvc — Tauri's externalBin suffix.
$Triple = (& rustc -vV | Select-String '^host:').ToString().Split()[-1]
$Arch   = $Triple.Split('-')[0]

# A running openworker-server.exe (e.g. a prior sidecar/smoke test) locks the output exe and
# makes PyInstaller's overwrite fail with Access-is-denied. Stop any before bundling.
$running = Get-Process -Name "openworker-server" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "==> stopping $($running.Count) running openworker-server process(es) holding the output exe"
    $running | Stop-Process -Force
    Start-Sleep -Seconds 1
}

# --- Authenticode signing (opt-in; absent credentials -> unsigned build, unchanged) ---------
# Resolution order mirrors the header: custom command > store thumbprint > base64 PFX.
# Empty env vars (unset repo secrets arrive as "") count as absent.

function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # Not on PATH (typical): newest x64 signtool from the installed Windows SDK.
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path $kits) {
        $tool = Get-ChildItem -Path $kits -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\' } |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($tool) { return $tool.FullName }
    }
    throw "signing credentials are configured but signtool.exe was not found - install the Windows 10/11 SDK."
}

$TimestampUrl = if ($env:WINDOWS_TIMESTAMP_URL) { $env:WINDOWS_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
$SignCommandTemplate = $null   # string with %1 -> signs one file; also handed to Tauri
$SignWindowsConfig   = $null   # merged into bundle.windows via the --config overlay

if ($env:WINDOWS_SIGN_COMMAND) {
    # Tauri substitutes %1 in signCommand's string form (split on spaces — a template
    # whose tool path contains spaces needs the {cmd,args} object form via a manual
    # overlay instead).
    $SignCommandTemplate = $env:WINDOWS_SIGN_COMMAND
    $SignWindowsConfig   = @{ signCommand = $SignCommandTemplate }
} else {
    $Thumbprint = $env:WINDOWS_CERTIFICATE_THUMBPRINT
    if (-not $Thumbprint -and $env:WINDOWS_CERTIFICATE) {
        # Base64 PFX (CI secret) -> import into the user store for this build; from
        # there on it is thumbprint signing, so the password never reaches a command
        # line or config file.
        $PfxPath = Join-Path ([IO.Path]::GetTempPath()) "ocw-signing.pfx"
        [IO.File]::WriteAllBytes($PfxPath, [Convert]::FromBase64String($env:WINDOWS_CERTIFICATE))
        try {
            $PfxPass = ConvertTo-SecureString -String $env:WINDOWS_CERTIFICATE_PASSWORD -AsPlainText -Force
            $Imported = Import-PfxCertificate -FilePath $PfxPath -CertStoreLocation Cert:\CurrentUser\My -Password $PfxPass
            $Thumbprint = $Imported.Thumbprint
        }
        finally { Remove-Item -Force $PfxPath -ErrorAction SilentlyContinue }
    }
    if ($Thumbprint) {
        $SignTool = Find-SignTool
        # signtool's own quoting handles the space-y SDK path; %1 marks the target file.
        $SignCommandTemplate = "`"$SignTool`" sign /sha1 $Thumbprint /fd sha256 /tr $TimestampUrl /td sha256 %1"
        # Native Tauri config (it locates signtool itself) rather than signCommand.
        $SignWindowsConfig = @{
            certificateThumbprint = $Thumbprint
            digestAlgorithm       = "sha256"
            timestampUrl          = $TimestampUrl
        }
    }
}

function Sign-File($path) {
    # Runs the resolved template on one file, then verifies the signature actually
    # took — a sign step that silently no-ops is worse than an unsigned build.
    $cmdline = $SignCommandTemplate -replace '%1', "`"$path`""
    Write-Host "    signing $path"
    cmd /c $cmdline
    if ($LASTEXITCODE -ne 0) { throw "signing failed (exit $LASTEXITCODE): $path" }
    $sig = Get-AuthenticodeSignature -FilePath $path
    if ($sig.Status -ne "Valid") {
        throw "signature verification failed for $path (status: $($sig.Status))"
    }
}

Write-Host "==> [1/4] PyInstaller: bundling openworker-server ($Triple)" -ForegroundColor Cyan
& $PyInst --noconfirm --clean `
    --distpath (Join-Path $Here "dist") --workpath (Join-Path $Here "build") `
    (Join-Path $Here "openworker-server.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

Write-Host "==> [2/4] staging sidecar resources" -ForegroundColor Cyan
# Onedir bundle (exe + _internal\) ships via Tauri `resources`, landing at <install>\sidecar\
# next to the app exe — onefile's per-launch self-extraction cost seconds of boot splash.
$BinDir = Join-Path $Gui "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$Src = Join-Path $Here "dist\openworker-server"
$Dst = Join-Path $BinDir "sidecar"
if (Test-Path $Dst) { Remove-Item -Recurse -Force $Dst }
# Clear any stale onefile binary from pre-onedir builds.
Remove-Item -Force (Join-Path $BinDir "openworker-server-$Triple.exe") -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force $Src $Dst
Write-Host "    -> $Dst"

Write-Host "==> [3/4] Authenticode signing (sidecar)" -ForegroundColor Cyan
if ($SignCommandTemplate) {
    # The sidecar ships via Tauri `resources`, which the bundler copies verbatim — it
    # signs only its own outputs (app exe, installers). Smart App Control evaluates
    # every PE at run time, so an unsigned sidecar fails on SAC machines even under a
    # signed installer. Sign the staged copy; the bundler picks it up from here.
    Sign-File (Join-Path $Dst "openworker-server.exe")
} else {
    Write-Host "    note: no signing credentials in env - installers will be UNSIGNED (SmartScreen warns; Smart App Control blocks). See docs/windows-signing.md." -ForegroundColor Yellow
}

Write-Host "==> [4/4] tauri build (--bundles $Bundles)" -ForegroundColor Cyan
# One --config overlay carries everything conditional: updater artifacts (needs the
# minisign key) and Authenticode config. An overlay FILE, not inline JSON (quotes are
# lost through the PowerShell -> npm.cmd -> cmd hop; "key must be a string", v0.1.3
# run) — and not tauri.conf.json itself, so dev builds on machines without any of
# these credentials keep working unchanged.
$OverlayCfg = @{ bundle = @{} }
if ($env:TAURI_SIGNING_PRIVATE_KEY) {
    $OverlayCfg.bundle.createUpdaterArtifacts = $true
} else {
    Write-Host "    WARNING: no updater signing key - building WITHOUT auto-update artifacts (not releasable)." -ForegroundColor Yellow
}
if ($SignWindowsConfig) {
    $OverlayCfg.bundle.windows = $SignWindowsConfig
}
$OverlayArgs = @()
if ($OverlayCfg.bundle.Count -gt 0) {
    $Overlay = Join-Path ([IO.Path]::GetTempPath()) "ocw-build-overlay.json"
    Set-Content -Path $Overlay -Value ($OverlayCfg | ConvertTo-Json -Depth 8) -Encoding ascii
    $OverlayArgs = @("--config", $Overlay)
}
Push-Location $Gui
try {
    & npm run tauri build -- --bundles $Bundles @OverlayArgs
    if ($LASTEXITCODE -ne 0) { throw "tauri build failed (exit $LASTEXITCODE)" }
}
finally {
    Pop-Location
}

if ($SignCommandTemplate) {
    # Belt-and-suspenders: fail the build if any produced installer somehow shipped
    # unsigned (e.g. a Tauri config regression) — CI must not release it silently.
    $BundleOut = Join-Path $Gui "src-tauri\target\release\bundle"
    Get-ChildItem -Path $BundleOut -Recurse -Include *.exe, *.msi -ErrorAction SilentlyContinue |
        ForEach-Object {
            $sig = Get-AuthenticodeSignature -FilePath $_.FullName
            if ($sig.Status -ne "Valid") {
                throw "unsigned artifact in a signed build: $($_.FullName) (status: $($sig.Status))"
            }
        }
    Write-Host "    all bundle artifacts verified signed" -ForegroundColor Green
}

$BundleDir = Join-Path $Gui "src-tauri\target\release\bundle"
Write-Host ""
Write-Host "Done. Installers under: $BundleDir" -ForegroundColor Green
Get-ChildItem -Path $BundleDir -Recurse -Include *.exe, *.msi -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host "  $($_.FullName)" }
