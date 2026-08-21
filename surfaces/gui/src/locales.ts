// Translation dictionary — all user-visible strings keyed by a typed TranslationKey.
//
// Adding a language: add it to the Locale union + the translations record, fill in the keys.
// Adding a string: add a key to TranslationKey, then to each locale's object. TypeScript will
// flag any locale missing a key at compile time.
export type Locale = "en" | "zh";

export type TranslationKey =
  // General / App
  | "app.boot.starting"
  | "app.boot.restoring"
  // Settings — nav
  | "settings.title"
  | "settings.tab.general"
  | "settings.tab.models"
  | "settings.tab.skills"
  | "settings.tab.voice"
  | "settings.tab.memory"
  | "settings.tab.personas"
  // Settings — General
  | "settings.general.title"
  | "settings.general.sub"
  | "settings.general.theme"
  | "settings.general.theme.light"
  | "settings.general.theme.dark"
  | "settings.general.theme.auto"
  | "settings.general.theme.help"
  | "settings.general.language"
  | "settings.general.language.help"
  | "settings.general.sidebar"
  | "settings.general.sidebar.peek"
  | "settings.general.sidebar.help"
  | "settings.general.files"
  | "settings.general.files.placeholder"
  | "settings.general.files.browse"
  | "settings.general.files.save"
  | "settings.general.files.help"
  | "settings.general.trusted"
  | "settings.general.trusted.help"
  | "settings.general.trusted.empty"
  | "settings.general.trusted.loading"
  | "settings.general.trusted.revoke"
  | "settings.general.alwaysOn"
  | "settings.general.autostart"
  | "settings.general.autostart.sub"
  | "settings.general.keepAwake"
  | "settings.general.keepAwake.sub"
  | "settings.general.setup"
  | "settings.general.setup.runAgain"
  | "settings.general.setup.checkUpdates"
  | "settings.general.setup.checking"
  | "settings.general.setup.latest"
  | "settings.general.setup.help"
  | "settings.general.composer"
  | "settings.general.composer.contextBar"
  | "settings.general.composer.contextBarSub"
  // Sidebar
  | "sidebar.newSession"
  | "sidebar.search"
  | "sidebar.showSidebar"
  | "sidebar.collapse"
  // Composer
  | "composer.placeholder.cowork"
  | "composer.placeholder.code"
  | "composer.placeholder.chat"
  | "composer.noModel"
  | "composer.send"
  | "composer.interrupt"
  // Chat hero
  | "hero.chat.greeting"
  | "hero.code.greeting"
  | "hero.suggest.head"
  // Common buttons
  | "common.cancel"
  | "common.save"
  | "common.delete"
  | "common.confirm"
  // Waiting / status
  | "status.waiting"
  | "status.compacting"
  | "status.interrupted"
  | "status.error"
  // Notice
  | "notice.maxIterations"
  | "notice.modelSwitched"
  | "notice.contextCompacted"
  | "notice.inputRejected"
  | "notice.messageRejected"
  // Jump to latest
  | "transcript.jumpLatest"
  // Automation toast
  | "toast.automationStarted"
  | "toast.viewRun"
  // Run banner
  | "runBanner.scheduled"
  | "runBanner.startedBy"
  | "runBanner.back"
  | "runBanner.untitled";

export const translations: Record<Locale, Record<TranslationKey, string>> = {
  en: {
    "app.boot.starting": "Starting OpenWorker…",
    "app.boot.restoring": "Restoring your session…",

    "settings.title": "Settings",
    "settings.tab.general": "General",
    "settings.tab.models": "Models",
    "settings.tab.skills": "Skills",
    "settings.tab.voice": "Voice input",
    "settings.tab.memory": "Memory",
    "settings.tab.personas": "Personas",

    "settings.general.title": "General",
    "settings.general.sub": "How OpenWorker looks and behaves on this machine.",
    "settings.general.theme": "Theme",
    "settings.general.theme.light": "Light",
    "settings.general.theme.dark": "Dark",
    "settings.general.theme.auto": "Auto",
    "settings.general.theme.help": "Auto follows your system appearance.",
    "settings.general.language": "Language",
    "settings.general.language.help": "Choose the interface language. Changes apply immediately.",
    "settings.general.sidebar": "Sidebar",
    "settings.general.sidebar.peek": "Conversations shown per coworker",
    "settings.general.sidebar.help": "Longer lists collapse behind \"Show more\". Applies per coworker and per project.",
    "settings.general.files": "Files",
    "settings.general.files.placeholder": "~/OpenWorker",
    "settings.general.files.browse": "Browse",
    "settings.general.files.save": "Save",
    "settings.general.files.help": "Each conversation gets its own folder under this location. Existing conversations keep their current folder; you can grant access to more folders inside any conversation.",
    "settings.general.trusted": "Trusted workspaces",
    "settings.general.trusted.help": "Trusted projects may manage their command allowances in .coworker/config.toml.",
    "settings.general.trusted.empty": "No workspaces are trusted.",
    "settings.general.trusted.loading": "Loading…",
    "settings.general.trusted.revoke": "Revoke",
    "settings.general.alwaysOn": "Always-on",
    "settings.general.autostart": "Open at login",
    "settings.general.autostart.sub": "Launch OpenWorker automatically when you sign in.",
    "settings.general.keepAwake": "Keep this system awake",
    "settings.general.keepAwake.sub": "Prevent idle sleep so scheduled tasks fire on time.",
    "settings.general.setup": "Setup & updates",
    "settings.general.setup.runAgain": "Run setup again",
    "settings.general.setup.checkUpdates": "Check for updates",
    "settings.general.setup.checking": "Checking…",
    "settings.general.setup.latest": "You're on the latest version.",
    "settings.general.setup.help": "Replays the first-run setup: model, first automation, tips.",
    "settings.general.composer": "Composer",
    "settings.general.composer.contextBar": "Show the context window bar",
    "settings.general.composer.contextBarSub": "A small meter showing how full the model's context window is. Turn it off to show this session's token total instead; either way the full breakdown is one click away.",

    "sidebar.newSession": "New session",
    "sidebar.search": "Search",
    "sidebar.showSidebar": "Show sidebar (⌘B)",
    "sidebar.collapse": "Show sidebar",

    "composer.placeholder.cowork": "Ask the coworker…  (drop or paste files)",
    "composer.placeholder.code": "Ask the coder to build, fix, or explain…  (drop or paste files)",
    "composer.placeholder.chat": "Ask anything…  (drop or paste files)",
    "composer.noModel": "No model connected",
    "composer.send": "Send",
    "composer.interrupt": "Interrupt",

    "hero.chat.greeting": "How can I help?",
    "hero.code.greeting": "Let's build something.",
    "hero.suggest.head": "Try a task",

    "common.cancel": "Cancel",
    "common.save": "Save",
    "common.delete": "Delete",
    "common.confirm": "Confirm",

    "status.waiting": "Waiting for agent...",
    "status.compacting": "Compacting context…",
    "status.interrupted": "Interrupted.",
    "status.error": "Error: {message}",

    "notice.maxIterations": "Stopped: max iterations reached.",
    "notice.modelSwitched": "Model switched",
    "notice.contextCompacted": "Context compacted",
    "notice.inputRejected": "That message was rejected.",
    "notice.messageRejected": "That message was rejected.",

    "transcript.jumpLatest": "Jump to latest",

    "toast.automationStarted": "Automation started",
    "toast.viewRun": "View run ›",

    "runBanner.scheduled": "Scheduled run",
    "runBanner.startedBy": "· started by an automation",
    "runBanner.back": "← Back to runs",
    "runBanner.untitled": "Automation",
  },

  zh: {
    "app.boot.starting": "正在启动 OpenWorker…",
    "app.boot.restoring": "正在恢复你的会话…",

    "settings.title": "设置",
    "settings.tab.general": "通用",
    "settings.tab.models": "模型",
    "settings.tab.skills": "技能",
    "settings.tab.voice": "语音输入",
    "settings.tab.memory": "记忆",
    "settings.tab.personas": "角色",

    "settings.general.title": "通用",
    "settings.general.sub": "OpenWorker 在这台电脑上的外观与行为。",
    "settings.general.theme": "主题",
    "settings.general.theme.light": "浅色",
    "settings.general.theme.dark": "深色",
    "settings.general.theme.auto": "跟随系统",
    "settings.general.theme.help": "跟随系统自动切换深浅色。",
    "settings.general.language": "语言",
    "settings.general.language.help": "选择界面语言，更改后立即生效。",
    "settings.general.sidebar": "侧边栏",
    "settings.general.sidebar.peek": "每个同事显示的对话数",
    "settings.general.sidebar.help": "更长的列表折叠在「显示更多」后面。按角色和项目分别应用。",
    "settings.general.files": "文件",
    "settings.general.files.placeholder": "~/OpenWorker",
    "settings.general.files.browse": "浏览",
    "settings.general.files.save": "保存",
    "settings.general.files.help": "每个对话在此位置下拥有自己的文件夹。已有对话保留当前文件夹；你可以在任何对话中授予更多文件夹的访问权限。",
    "settings.general.trusted": "受信任的工作区",
    "settings.general.trusted.help": "受信任的项目可在 .coworker/config.toml 中管理命令权限。",
    "settings.general.trusted.empty": "没有受信任的工作区。",
    "settings.general.trusted.loading": "加载中…",
    "settings.general.trusted.revoke": "撤销",
    "settings.general.alwaysOn": "常驻",
    "settings.general.autostart": "开机自启",
    "settings.general.autostart.sub": "登录时自动启动 OpenWorker。",
    "settings.general.keepAwake": "保持系统唤醒",
    "settings.general.keepAwake.sub": "防止休眠，确保定时任务按时执行。",
    "settings.general.setup": "设置与更新",
    "settings.general.setup.runAgain": "重新运行设置",
    "settings.general.setup.checkUpdates": "检查更新",
    "settings.general.setup.checking": "检查中…",
    "settings.general.setup.latest": "已是最新版本。",
    "settings.general.setup.help": "重新运行首次设置：模型、第一个自动化任务、提示。",
    "settings.general.composer": "编辑器",
    "settings.general.composer.contextBar": "显示上下文窗口进度条",
    "settings.general.composer.contextBarSub": "显示模型上下文窗口使用率的小型进度条。关闭后改为显示本次会话的 token 总量；无论哪种方式，完整明细都只需一次点击即可查看。",

    "sidebar.newSession": "新建会话",
    "sidebar.search": "搜索",
    "sidebar.showSidebar": "显示侧边栏 (⌘B)",
    "sidebar.collapse": "显示侧边栏",

    "composer.placeholder.cowork": "向同事提问…  (拖放或粘贴文件)",
    "composer.placeholder.code": "让程序员构建、修复或解释…  (拖放或粘贴文件)",
    "composer.placeholder.chat": "随便问…  (拖放或粘贴文件)",
    "composer.noModel": "未连接模型",
    "composer.send": "发送",
    "composer.interrupt": "中断",

    "hero.chat.greeting": "有什么可以帮你的？",
    "hero.code.greeting": "来构建点什么吧。",
    "hero.suggest.head": "试试这些任务",

    "common.cancel": "取消",
    "common.save": "保存",
    "common.delete": "删除",
    "common.confirm": "确认",

    "status.waiting": "等待代理响应...",
    "status.compacting": "正在压缩上下文…",
    "status.interrupted": "已中断。",
    "status.error": "错误：{message}",

    "notice.maxIterations": "已停止：达到最大迭代次数。",
    "notice.modelSwitched": "已切换模型",
    "notice.contextCompacted": "上下文已压缩",
    "notice.inputRejected": "该消息被拒绝。",
    "notice.messageRejected": "该消息被拒绝。",

    "transcript.jumpLatest": "跳到最新",

    "toast.automationStarted": "自动化任务已启动",
    "toast.viewRun": "查看运行 ›",

    "runBanner.scheduled": "定时运行",
    "runBanner.startedBy": "· 由自动化任务启动",
    "runBanner.back": "← 返回运行列表",
    "runBanner.untitled": "自动化任务",
  },
};
