# Fork 维护：自动跟版 + 自动更新

本仓库是 [andrewyng/openworker](https://github.com/andrewyng/openworker) 的社区简体中文 Fork。目标：

1. **自动跟版**：上游发 `vX.Y.Z` 后，CI 合入 `i18n-simplified-chinese` 并打 `vX.Y.Z-zh.N` 包。
2. **自动更新**：安装包轮询**本 Fork** 的 Releases，不再指向官方英文频道。

## 一次配置（必须）

### 1. 写入 updater 签名私钥

发布流水线用 minisign 签名更新包。公钥已写入 `surfaces/gui/src-tauri/tauri.conf.json`；**私钥只放在 GitHub Secrets**，不要提交。

本地已生成密钥（若你是从本机这次改动继续）：

```text
.secrets/openworker-zh-updater.key      # 私钥（勿提交，已在 .gitignore）
.secrets/openworker-zh-updater.key.pub  # 公钥
```

在 GitHub 仓库 **Settings → Secrets and variables → Actions** 新增：

| Secret 名 | 值 |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | `.secrets/openworker-zh-updater.key` 的**全文** |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 可留空（当前密钥无密码） |

用 GitHub CLI 也可以：

```bash
gh secret set TAURI_SIGNING_PRIVATE_KEY < .secrets/openworker-zh-updater.key
```

若密钥丢失，只能重新 `npx tauri signer generate`，更新 `tauri.conf.json` 里的 `pubkey`，并让所有用户**手动重装一次**。

### 2. 打开仓库 Issues（可选但推荐）

Settings → General → Features → **Issues**。同步冲突时 CI 会开 Issue；关掉也能在 Actions 日志里看到失败。

### 3. 确认 Actions 权限

Settings → Actions → General → Workflow permissions → **Read and write permissions**。

## 版本号规则

| 场景 | `tauri.conf.json` / tag |
|---|---|
| 上游 `0.2.1` 的第 3 个中文构建 | `0.2.1-zh.3` / `v0.2.1-zh.3` |
| 上游升到 `0.2.2` 的首个中文构建 | `0.2.2-zh.1` / `v0.2.2-zh.1` |

应用内版本必须与 tag 去掉 `v` 后完全一致，自动更新才会对得上。

## 日常怎么跑

### 自动（推荐）

- Workflow **Sync upstream**：每天 UTC 08:00，或 Actions 里手动 Run。
- 成功后会 push `v*-zh.N` tag → 触发 **Release** 打 macOS/Windows 包并发布（非 draft）。
- 装好的汉化版会从  
  `https://github.com/cr-yijieshusheng/openworker/releases/latest/download/latest.json`  
  拉更新。

### 手动

```bash
git remote add upstream https://github.com/andrewyng/openworker.git  # 一次即可
git fetch upstream --tags
git checkout i18n-simplified-chinese

# 预览
DRY_RUN=1 packaging/sync_upstream.sh

# 同步最新上游 tag 并推送发布 tag
CREATE_TAG=1 packaging/sync_upstream.sh

# 或指定上游版本；FORCE=1 可在同一上游基线上再打一版 zh.N
FORCE=1 CREATE_TAG=1 packaging/sync_upstream.sh v0.2.1
```

合并冲突时脚本会停住。解决后：

```bash
python3 packaging/apply_fork_updater.py --version 0.2.2-zh.1
git add -A && git commit
CREATE_TAG=1 packaging/sync_upstream.sh v0.2.2
```

`apply_fork_updater.py` 会把 updater 的 endpoint / pubkey 重新钉回本 Fork，避免被上游配置盖掉。

## 从旧版手动包迁到自动更新

此前 `v0.2.1-zh.2` 应用内版本仍是 `0.2.1`，且 updater 指向官方或已关闭。  
**请手动安装一次 `v0.2.1-zh.3`（或更新）**；之后即可走本 Fork 自动更新。

## 相关文件

- `.github/workflows/sync-upstream.yml` — 跟版
- `.github/workflows/release.yml` — 打包 / `latest.json`
- `packaging/sync_upstream.sh` — 本地/CI 共用同步脚本
- `packaging/apply_fork_updater.py` — 钉死 Fork 更新通道
- `surfaces/gui/src-tauri/tauri.conf.json` — `version` + `plugins.updater`
