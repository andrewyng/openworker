# AGENTS.md - 项目备忘

本项目基于 andrewyng/openworker 二次开发，品牌名 Gamer Worker。

## 提交检查清单（每次必做）

1. **更新 CHANGELOG.md** - 每次提交前先更新日志，不要提交后补
2. **检查敏感信息** - 提交前运行：
   ```bash
   git diff --cached | grep -iE 'ark-[0-9a-f]{8}|sk-[a-zA-Z0-9]{15}|password|secret.*=' | grep -v 'placeholder|label|sk-ant|sk-or|sk-\.\.'
   ```
3. **API key 只在 ~/.config/coworker/secrets.json** - 仓库外，绝不提交
4. **.DS_Store 已在 .gitignore** - 确认不会意外提交
5. **.github/workflows/ 不可推送** - gh token 无 workflow scope，合并上游时跳过此文件

## 运行环境

- 后端：`/Users/bobo/codex_person/openworker/.venv/bin/openworker-server --cwd /Users/bobo/codex_person/openworker-workspace --port 8765`
- 前端：`cd surfaces/gui && npm run dev`（http://localhost:1420）
- 后端重启后必须重启 Vite（token 会变，否则白屏）
- 模型：火山引擎 GLM-5.2，配置在 ~/.config/coworker/config.toml + secrets.json

## Skill 系统

- 全局 Skill 目录：`~/.config/coworker/skills/`
- 仓库内 Skill 目录：`skills/`（需手动同步到全局目录）
- `/` 指令在 Composer.tsx 中实现，两步式操作（先选 Skill 再输入意图）
- Skill 弹窗支持模糊搜索、上下键导航、scrollIntoView

## 上游同步

- 上游仓库：https://github.com/andrewyng/openworker
- remote 名：upstream
- 同步：`git fetch upstream && git merge upstream/main`
- 冲突通常在 tauri.conf.json / App.tsx / Composer.tsx，保留中文翻译 + 采用上游新功能
