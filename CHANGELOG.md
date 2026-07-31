# 更新日志

本项目基于 [andrewyng/openworker](https://github.com/andrewyng/openworker) 二次开发，感谢原作者 Andrew Ng 及 OpenWorker 团队的开源贡献。

## [0.3.0] - 2026-07-31

### 新增
- **Code agent 系统提示词大幅增强**：新增测试、调试、代码质量自检三大模块
  - Testing：测试优先思维、用例设计（正常/边界/异常/回归）、AAA 模式、验证流程
  - Debugging：五步调试法（复现 -> 隔离 -> 假设 -> 修复 -> 验证）
  - Code Quality：完成前自检清单（编译、类型、错误处理、密钥泄露等）
- **内置 5 个编程 Skill**（通过 `/` 指令激活）：
  - `/tdd` - 测试驱动开发：红绿重构循环，先写测试再写实现
  - `/review` - 代码审查：安全/性能/可维护性/测试覆盖全面检查
  - `/debug` - 系统化调试：复现/隔离/假设/修复/验证五步法
  - `/test-gen` - 测试生成：自动生成正常/边界/异常路径测试用例
  - `/refactor` - 安全重构：提取/内联/简化/移动，保持行为不变
- 同步上游 andrewyng/openworker v0.1.7（SSRF 防护、artifact 扫描修复、上下文压缩等）

### 变更
- Code agent 编辑策略增强：多文件改动顺序规划、todo 跟踪

## [0.2.0] - 2026-07-30

### 新增
- **Skill 快捷指令系统**：在输入框输入 `/` 即可弹出 Skill 列表
  - 支持模糊搜索匹配（如输入 `/p` 自动匹配 `pcg`）
  - `↑↓` 键导航选择，`Enter` 或 `Tab` 确认选中
  - 两步式操作：先选 Skill 再输入意图，最后 `Enter` 发送
  - `Esc` 关闭弹窗
  - 选中后自动将 Skill 指令注入消息，模型按 Skill 指令执行
- 接入火山引擎 GLM-5.2 模型（通过 OpenAI 兼容接口）
- 配置本地开发环境（Python 3.12 + Node 26 + Vite）

### 变更
- **品牌更名**：将 UI 界面中的 "OpenWorker" 统一替换为 "Gamer Worker"
  - 侧边栏品牌标识、引导页标题、启动画面
  - 设置页、更新提示、连接器页面等全部 UI 文本
  - index.html 页面标题、Tauri 配置（productName/publisher）
  - 保留 API 协议头 `X-OpenWorker-Token` 以保持后端兼容
- **UI 全面的简体中文**：将所有用户可见的英文界面文本翻译为简体中文
  - 侧边栏（Sidebar）：导航、会话列表、账户菜单、分组筛选、时间显示等
  - 主界面（App）：顶栏、对话框、引导页、占位符、状态提示、Toast 消息等
  - 输入框（Composer）：附件、语音、模型选择、Token 用量、发送/停止等
  - 右侧面板（RightRail）：进度、产出、预览器、文件操作等
  - 对话记录（Transcript）：助手标签、审批卡片、计划卡片等
  - 设置页（SettingsView）：通用设置、外观、模型配置等
  - 引导页（Onboarding）：欢迎、连接工具、完成设置等
  - 连接器页面（Connectors）：Slack/GitHub/Gmail/HubSpot/Calendar 等全部连接器详情页
  - 其他组件：收件箱、定时任务、搜索、权限审批、文件夹管理等 30+ 个组件文件
- 品牌名（Slack、GitHub 等）和技术术语（API、PDF、OAuth 等）保留英文
- 时间格式中文化（刚刚/分钟/小时/天/周/月/年）
- 将 `.DS_Store` 加入 `.gitignore`

### 修复
- 修复后端服务器重启后 token 失效导致前端白屏的问题

## [0.1.0] - 2026-07-29

### 初始版本
- Fork 自 [andrewyng/openworker](https://github.com/andrewyng/openworker) v0.1.6
