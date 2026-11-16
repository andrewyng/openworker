param([Parameter(Mandatory=$true)][string]$NativeRoot,[Parameter(Mandatory=$true)][string]$BuildCache,[switch]$SkipInstall,[switch]$Package,[switch]$SkipUnit)
# SkipUnit is for a split qualification only: first run the same storage/unit suite
# in a privileged test process for Windows file-symlink fixtures. Run app/UI tests
# as the regular desktop user; do not elevate Arcade or change system policy.
$ErrorActionPreference='Continue'
$env:CARGO_HOME=Join-Path $env:USERPROFILE '.cargo'
$env:RUSTUP_HOME=Join-Path $env:USERPROFILE '.rustup'
$env:PATH="$env:CARGO_HOME\bin;C:\Program Files\nodejs;C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\Llvm\ARM64\bin;$env:PATH"
$env:CARGO_BUILD_JOBS='4'
$env:CARGO_TARGET_DIR=$BuildCache
$env:CSC_IDENTITY_AUTO_DISCOVERY='false'
function Check-Exit { if($LASTEXITCODE -ne 0){throw "Command failed: $LASTEXITCODE"} }
try {
 Set-Location $NativeRoot -ErrorAction Stop
 cargo +1.96.0 build --locked --release -p openworker-sdk --bin openworker-harness
 Check-Exit
 if(-not $SkipInstall){npm.cmd --prefix typescript-sdk ci;Check-Exit}
 Set-Location examples\arcade
 if(-not $SkipInstall){npm.cmd ci;Check-Exit}
 $env:OPENWORKER_RUNTIME=Join-Path $BuildCache 'release\openworker-harness.exe'
 npm.cmd run build;Check-Exit
 if(-not $SkipUnit){npm.cmd test;Check-Exit}
 npm.cmd run test:integration;Check-Exit
 npm.cmd run test:starters;Check-Exit
 node test\playtest-ui.mjs;Check-Exit
 node test\playtest-app-ui.mjs;Check-Exit
 node test\review-ui.mjs;Check-Exit
 if($Package){
  $env:ARCADE_TARGET_ARCH='arm64'
  npm.cmd run runtime;Check-Exit
  node scripts\package-dev.mjs release-review
  Check-Exit
  $env:ARCADE_PACKAGED_APP=Join-Path (Get-Location) 'release-review\win-arm64-unpacked\Arcade Studio.exe'
  if(-not (Test-Path $env:ARCADE_PACKAGED_APP)){throw 'Packaged app missing'}
  node test\playtest-app-ui.mjs;Check-Exit
  node test\review-ui.mjs;Check-Exit
 }
 Write-Output 'ARCADE_REVIEW_WINDOWS_PASSED'
 exit 0
}catch {Write-Output "ARCADE_REVIEW_WINDOWS_FAILED: $_";exit 1}
