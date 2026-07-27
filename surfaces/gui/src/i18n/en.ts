// English strings for OpenWorker GUI
const en = {
  // App shell
  app: {
    title: "OpenWorker",
    newSession: "New session",
    search: "Search sessions…",
    settings: "Settings",
    integrations: "Integrations",
    activity: "Activity",
    inbox: "Inbox",
    scheduled: "Scheduled",
  },

  // Settings
  settings: {
    title: "Settings",
    general: "General",
    generalSub: "How OpenWorker looks and behaves on this machine.",
    models: "Models",
    voiceInput: "Voice input",
    personas: "Personas",
    language: "Language",
    languageSub: "Choose your preferred language.",
    files: "Files",
    filesDesc: "Default location for new files created by OpenWorker.",
    scratchBase: "Scratch base folder",
    pdf: "PDF handling",
    pdfDesc: "When a model cannot process PDFs natively, OpenWorker can extract text or rasterize pages.",
    extractText: "Extract text (fast)",
    rasterize: "Rasterize pages (slower, preserves layout)",
    openFolder: "Open folder",
    onboarding: "Onboarding checklist",

    // Models
    modelProvider: "Model provider",
    apiKey: "API key",
    model: "Model",
    addProvider: "Add provider…",
    testConnection: "Test connection",

    // Voice
    voiceSetup: "Voice Input setup is available in the OpenWorker desktop app.",
    downloadModel: "Download model",
    deleteModel: "Delete model",
    downloading: "Downloading…",
    voiceError: "Voice Input could not complete that action",
    confirmDeleteVoice: "Delete the local Whisper model and disable Voice Input?",

    // Appearance
    theme: "Theme",
    themeSystem: "System",
    themeLight: "Light",
    themeDark: "Dark",
    sessionsPeek: "Session previews",
    sessionsPeekDesc: "Show a preview of recent sessions in the sidebar.",

    // Trusted workspaces
    trustedWorkspaces: "Trusted workspaces",
    trustedWorkspacesDesc: "Folders where commands run without asking each time.",
    removeTrust: "Remove",
    addFolder: "Add folder…",
    noTrustedFolders: "No trusted folders yet.",
  },

  // Session
  session: {
    newSession: "New session",
    noSessions: "No sessions yet. Start one to see it here.",
    typing: "Typing…",
    thinking: "Thinking…",
    you: "You",
    assistant: "Assistant",
    send: "Send",
    attach: "Attach",
    stop: "Stop",
    retry: "Retry",
    copy: "Copy",
    delete: "Delete",
    rename: "Rename",
    today: "Today",
    yesterday: "Yesterday",
    older: "Older",
  },

  // Composer
  composer: {
    placeholder: "Ask OpenWorker to do something…",
    voiceHint: "Hold to speak",
    send: "Send",
    stop: "Stop",
    attachFile: "Attach file",
    attachFolder: "Attach folder",
  },

  // Integrations
  integrations: {
    title: "Integrations",
    subtitle: "Connect tools and services OpenWorker can use.",
    connected: "Connected",
    connect: "Connect",
    disconnect: "Disconnect",
    configure: "Configure",
    addConnection: "Add connection",
  },

  // Common
  common: {
    save: "Save",
    cancel: "Cancel",
    close: "Close",
    back: "Back",
    done: "Done",
    remove: "Remove",
    add: "Add",
    edit: "Edit",
    confirm: "Confirm",
    loading: "Loading…",
    error: "Something went wrong",
    retry: "Try again",
    more: "More",
    less: "Less",
    on: "On",
    off: "Off",
    enabled: "Enabled",
    disabled: "Disabled",
    yes: "Yes",
    no: "No",
    ok: "OK",
  },

  // Automation
  automation: {
    title: "Automation",
    quickstart: "Schedule recurring work",
    quickstartDesc: "Set up a morning briefing, a weekly report, or any routine task.",
  },

  // Onboarding
  onboarding: {
    title: "Welcome to OpenWorker",
    step1: "Add a model provider",
    step2: "Connect your tools",
    step3: "Try your first task",
    done: "You're all set!",
  },
} as const;

export default en;
export type I18nStrings = typeof en;
