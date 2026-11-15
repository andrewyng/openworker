param([string]$NativeRoot, [string]$RustUserRoot = $env:USERPROFILE, [ValidateSet('arm64','x64')][string]$TargetArch = 'arm64', [switch]$Package, [switch]$SkipInstall)
# Windows PowerShell 5 treats redirected native stderr (including Cargo progress)
# as errors. Inspect each native exit code instead of terminating on progress.
$ErrorActionPreference = 'Continue'
$env:CARGO_HOME = Join-Path $RustUserRoot '.cargo'
$env:RUSTUP_HOME = Join-Path $RustUserRoot '.rustup'
$env:PATH = "$env:CARGO_HOME\bin;C:\Program Files\nodejs;C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\Llvm\ARM64\bin;$env:PATH"
$env:CARGO_BUILD_JOBS = '4'
$env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed: $LASTEXITCODE" } }
Set-Location $NativeRoot -ErrorAction Stop
cargo +1.96.0 build --locked --release -p openworker-sdk --bin openworker-harness
Check-Exit
if (-not $SkipInstall) { npm.cmd --prefix typescript-sdk ci; Check-Exit }
Set-Location examples\arcade
if (-not $SkipInstall) { npm.cmd ci; Check-Exit }
$env:OPENWORKER_RUNTIME = Join-Path $NativeRoot 'target\release\openworker-harness.exe'
npm.cmd run build
Check-Exit
npm.cmd test
Check-Exit
npm.cmd run test:integration
Check-Exit
npm.cmd run test:starters
Check-Exit
node test\offline-ui.mjs
Check-Exit
node test\ui.mjs
Check-Exit
node test\studio-ui.mjs
Check-Exit
node test\discovery-ui.mjs
Check-Exit
node test\skybound-ui.mjs
Check-Exit
if ($Package) {
  $env:ARCADE_TARGET_ARCH = $TargetArch
  npm.cmd run package -- "--$TargetArch"
  Check-Exit
  $env:ARCADE_PACKAGED_APP = (Get-ChildItem release -Filter 'Arcade Studio.exe' -Recurse | Select-Object -First 1).FullName
  if (-not $env:ARCADE_PACKAGED_APP) { throw 'Packaged app not found' }
  node test\ui.mjs
  Check-Exit
  node test\studio-ui.mjs
  Check-Exit
  node test\discovery-ui.mjs
  Check-Exit
  node test\skybound-ui.mjs
  Check-Exit
}
Write-Output 'ARCADE_WINDOWS_QUALIFICATION_PASSED'
