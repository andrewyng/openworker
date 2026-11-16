param([ValidateSet('check','build','release','test','clippy')][string]$Action='check', [ValidateSet('native','x64')][string]$Architecture='native')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Set-Location (Join-Path $PSScriptRoot '..')
$clang=Get-ChildItem 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\Llvm' -Filter clang.exe -Recurse | Select-Object -First 1
if(-not $clang){throw 'Clang is required to build ring for Windows ARM64'}
$env:PATH=$clang.DirectoryName+';'+$env:PATH
$env:CARGO_BUILD_JOBS='2'
$env:CARGO_PROFILE_DEV_DEBUG='0'
$env:CARGO_PROFILE_TEST_DEBUG='0'
$env:CARGO_INCREMENTAL='0'
Write-Output ('Compiler: '+$clang.FullName)
rustc +stable -V
$targetArgs=@()
if($Architecture -eq 'x64'){$targetArgs=@('--target','x86_64-pc-windows-msvc')}
if($Action -eq 'test') {
  cargo +stable test --locked @targetArgs -p openworker-runtime --test windows --test servers --test connections --test model_connections --test session_grants
  if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
  cargo +stable test --locked @targetArgs -p openworker-tools -p notification-tools -p device-tools -p computer-tools --lib
  if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
  cargo +stable test --locked @targetArgs -p openworker-sdk --features test-support
} elseif($Action -eq 'clippy') {
  cargo +stable clippy --locked @targetArgs --workspace --lib --bins -- -D warnings
} elseif($Action -eq 'release') {
  cargo +stable build --locked @targetArgs --release --bin openworker --bin openworker-harness --bin native-tools-runtime
} elseif($Action -eq 'build') {
  cargo +stable build --locked @targetArgs --bin openworker --bin openworker-harness --bin native-tools-runtime
} else {
  cargo +stable check --locked @targetArgs --workspace
}
exit $LASTEXITCODE
