# OpenCode Server Smoke Test Plan

## Objective

驗證 PR #110 compatibility slice 在實際啟動本機 server 後的基本運作，特別是：

- server 可正常啟動並通過 health check；
- sidecar token authentication 正常；
- OpenCode Zen / Go provider cards 可由 server 回傳；
- Zen / Go profile、configured state、key 與移除行為彼此隔離；
- API response 不會暴露 API key；
- GUI 可以連線到本機 server。

本文件不測試真實 OpenCode API、真實 API key、credits 或 live model completion。

## Prerequisites

請在隔離的 PR110 worktree 執行：

```text
C:\Users\Cheney\Documents\Github\openworker-pr110
```

確認以下工具已安裝：

```powershell
python --version
node --version
pnpm --version
```

不要使用 private repository 的 `.venv\Scripts\openworker-server.exe`。該環境是 editable-installed 到 private repository，可能啟動錯誤的 source tree。

## 1. Create Isolated Environment

從 PR110 worktree root 執行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -e ".[messaging,dev]"
```

若 `.venv` 已存在，可跳過建立與安裝步驟，但應確認它是由 PR110 worktree 建立的環境。

## 2. Start The Server

使用隔離 state directory，避免修改日常 OpenWorker 設定：

```powershell
New-Item -ItemType Directory -Force temp\server-state | Out-Null
$env:COWORKER_STATE_DIR = "$PWD\temp\server-state"
.\.venv\Scripts\openworker-server.exe --cwd "$PWD" --port 8765
```

預期輸出包含：

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8765
```

保持此 terminal 執行 server，另開一個 PowerShell 執行後續步驟。

## 3. Health And Authentication Smoke Test

`/v1/health` 不需要 sidecar token：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/v1/health
```

讀取 server 產生的 token：

```powershell
$token = Get-Content temp\server-state\sidecar-8765.token
$headers = @{ "x-openworker-token" = $token }
```

沒有 token 時，受保護的 provider endpoint 應拒絕請求：

```powershell
try {
  Invoke-RestMethod http://127.0.0.1:8765/v1/providers
} catch {
  $_.Exception.Response.StatusCode.value__
}
```

帶上 token 後應成功：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8765/v1/providers `
  -Headers $headers
```

## 4. Verify OpenCode Provider Descriptors

取得 provider 清單：

```powershell
$providers = Invoke-RestMethod `
  -Uri http://127.0.0.1:8765/v1/providers `
  -Headers $headers

$providers | Where-Object { $_.name -in @("opencode_zen", "opencode_go") } |
  Select-Object name, title, endpoint, configured, values, suggested_models
```

確認：

- `opencode_zen` 與 `opencode_go` 都存在；
- endpoint 不同；
- 初始 `configured` 狀態符合 isolated state；
- `values` 不包含 API key；
- suggested models 使用正確 provider prefix。

## 5. Test Independent Profile Save

使用測試 key，不使用真實 credentials：

```powershell
$body = @{
  name = "opencode_zen"
  fields = @{ api_key = "test-zen-key" }
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/v1/providers `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

再設定 Go：

```powershell
$body = @{
  name = "opencode_go"
  fields = @{ api_key = "test-go-key" }
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/v1/providers `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

重新查詢 provider 清單：

```powershell
$providers = Invoke-RestMethod `
  -Uri http://127.0.0.1:8765/v1/providers `
  -Headers $headers

$providers | Where-Object { $_.name -in @("opencode_zen", "opencode_go") } |
  Select-Object name, configured, values
```

預期結果：

- Zen 與 Go 都是 `configured = true`；
- Zen 的設定不會覆蓋 Go；
- Go 的設定不會覆蓋 Zen；
- response 中不會出現 `test-zen-key` 或 `test-go-key`；
- state directory 下不應出現 `provider:opencode` shared profile。

## 6. Test Independent Profile Removal

移除 Zen：

```powershell
Invoke-RestMethod `
  -Method Delete `
  -Uri http://127.0.0.1:8765/v1/providers/opencode_zen `
  -Headers $headers
```

重新查詢並確認：

- Zen 變成未 configured；
- Go 仍保持 configured；
- Go 的 key 與 provider state 未被刪除。

再移除 Go：

```powershell
Invoke-RestMethod `
  -Method Delete `
  -Uri http://127.0.0.1:8765/v1/providers/opencode_go `
  -Headers $headers
```

確認兩個 provider 都回到未 configured 狀態。

## 7. Verify Endpoint Failure Behavior

沒有提供 key 時，verify 應在 server 層明確回傳錯誤，而不是成功：

```powershell
$body = @{ name = "opencode_zen"; fields = @{} } | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/v1/providers/verify `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

預期回應包含類似：

```text
Enter an API key to test.
```

成功的 `/models` verification 必須使用 local HTTP mock 或既有 mock-based tests，不應使用真實 OpenCode endpoint。

## 8. Connect The GUI

保持 server 執行，另開 terminal：

```powershell
cd surfaces\gui
pnpm dev
```

在瀏覽器開啟 Vite 顯示的網址，通常是：

```text
http://localhost:1420
```

GUI 中確認：

- OpenCode Zen 與 Go 兩張 card 都顯示；
- 兩張 card 可分別開啟設定；
- Zen help link 是 `https://opencode.ai/zen`；
- Go help link 是 `https://opencode.ai/go`；
- 設定一個 provider 不會標記另一個 provider；
- 移除一個 provider 不會清除另一個 provider。

## 9. Automated Validation Before And After Smoke Test

server smoke test 前後執行：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_opencode_contract.py `
  tests/test_provider_router.py `
  tests/test_providers.py `
  tests/test_provider_verify.py -q
```

GUI 驗證：

```powershell
cd surfaces\gui
pnpm vitest run
pnpm run build
```

預期結果目前為：

- Backend：`132 passed`；
- GUI：`100 passed`；
- GUI build/typecheck：成功。

## 10. Cleanup

停止 server：

```text
Ctrl+C
```

若仍有殘留行程：

```powershell
Get-Process -Name "openworker-server" -ErrorAction SilentlyContinue |
  Stop-Process -Force
```

刪除本次隔離 state：

```powershell
Remove-Item -Recurse -Force temp\server-state
```

不要提交以下內容：

- `.venv/`；
- `temp/server-state/`；
- sidecar token；
- API keys 或 live responses；
- `pnpm-lock.yaml`、`pnpm-workspace.yaml` 或其他測試產生的 lockfiles；
- build output、coverage report 或 cache。

## Explicit Boundary

本文件的 server test 是本機 smoke/integration test，不代表已驗證 real OpenCode service。

下列項目仍需 mock-based 或受明確授權後才能進行：

- 真實 `/models` verification；
- 真實 `/chat/completions` request；
- streaming completion；
- tool-call continuation；
- credits、rate limits 或 provider-specific live errors。
