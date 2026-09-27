import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { getI18n, Trans, useTranslation } from "react-i18next";
import { getStoredLanguage, setLanguage as setI18nLanguage, type Lang } from "../i18n";
import {
  announceCloudChanged,
  CLOUD_CHANGED,
  cloudLogin,
  getCloudConnections,
  getConnectors,
  cloudLogout,
  getActiveOrg,
  getCloudMachines,
  getCloudStatus,
  getMachines,
  MACHINES_CHANGED,
  getMe,
  isCloudMode,
  waitForCloudSignIn,
  type CloudStatus,
  type Machine,
  type MeInfo,
  getSandboxSettings,
  getSettings,
  getTrustedWorkspaces,
  setSandboxSettings,
  setAutoApprove,
  setAutoApproveShadow,
  setCompactionSettings,
  setContextBar,
  setOnboarded,
  setPdfSettings,
  setScratchBase,
  setSessionsPeek,
  setWorkspaceTrusted,
  type CompactionSettings,
  type ModelSettings,
  type PdfSettings,
  type SandboxCredentialEntry,
  type SandboxSettings,
  type WorkspaceCommandTrust,
} from "../api";
import {
  cancelDictationModelDownload,
  deleteDictationModel,
  downloadDictationModel,
  getAutostart,
  getDictationStatus,
  getKeepAwake,
  checkForUpdate,
  installUpdate,
  isTauri,
  listenDictationDownloadProgress,
  markDictationTestPassed,
  pickFolder,
  setAutostart,
  setKeepAwake,
  startDictation,
  stopDictation,
  verifyDictationModel,
  type DictationDownloadProgress,
  type DictationStatus,
} from "../tauri";
import { canSignOut, cloudSignOut } from "../cloudAuth";
import { reflectSettings, settingsMachineParam } from "../routes";
import { useThemePref } from "../theme";
import { useTextSize } from "../textSize";
import { createPortal } from "react-dom";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";
import { ConnectorGlance } from "./connectors/ConnectorGlance";
import { ModelsTab } from "./ManageTabs";
import { ConnectorsSection } from "./connectors/ConnectorsSection";
import { MachineModelsPanel } from "./MachineModelsPanel";
import { MachinesSection } from "./MachinesSection";
import { RemoteConnectorsPanel } from "./RemoteConnectorsPanel";
import { MemorySection } from "./MemorySection";
import { PersonasTab } from "./PersonasTab";
import { SkillsTab } from "./SkillsTab";
import { showPersonas } from "../flags";

// Settings, restructured (Option 2) into a full-page surface that mirrors IntegrationsView's shell:
// a left sub-nav (Appearance · Files · Models · Personas) + centered panel, replacing the old
// top-tab ManageModal. Local/app concerns live here; anything external (Connectors, Messaging, MCP,
// Activity) stays under Integrations. Appearance + Files are re-skinned to the mock's Tailwind idiom;
// Models + Personas host the existing tab components inside the page shell (field re-skin to follow).
// "appearance" is the General tab's stable key — callers deep-link with it, so the
// rename (UX-021) changed only the label. "files" folded into General as a card.
export type SetTab =
  | "appearance"
  | "account"
  | "models"
  | "context"
  | "skills"
  | "voice"
  | "memory"
  | "sandbox"
  | "machines"
  | "connectors"
  | "personas"
  // UX-049: per-connector glance pages (App group), shown once connected somewhere.
  | "slack"
  | "github";

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-ui font-medium text-ink";
const FIELD_HELP = "text-meta text-muted mt-1.5 leading-relaxed";
const INPUT =
  "flex-1 min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent";
const BTN_ACCENT = "text-ui px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-ui px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";

// UX-046 (approved 2026-08-30): the rail is grouped by SCOPE. APP pages are
// about where you sit; MACHINE pages are about one machine's engine — the
// picker on that group header chooses which ("This Mac" is just the first
// machine); INVENTORY is the fleet list itself, outside the picker. With no
// enrolled machines the picker does not exist and the rail reads like before.
type SetIcon =
  | "sliders"
  | "code"
  | "mic"
  | "archive"
  | "sparkle"
  | "book"
  | "refresh"
  | "plug"
  | "shield"
  | "user";
// `labelKey` = the i18n key for the label; tabs without one yet show `label` as is.
type TabDef = { key: SetTab; label: string; labelKey?: string; icon: SetIcon };

const SET_GROUPS: { name: string; nameKey?: string; scope: "app" | "machine" | "inventory"; tabs: TabDef[] }[] = [
  {
    name: "App",
    nameKey: "settingsx.group.app",
    scope: "app",
    tabs: [
      { key: "appearance", label: "General", labelKey: "settings.tab.general", icon: "sliders" },
      { key: "voice", label: "Voice input", labelKey: "settings.tab.voice", icon: "mic" },
      { key: "account", label: "Account", labelKey: "settingsx.tab.account", icon: "user" },
      { key: "slack", label: "Slack", labelKey: "settingsx.tab.slack", icon: "plug" },
      { key: "github", label: "GitHub", labelKey: "settingsx.tab.github", icon: "plug" },
    ],
  },
  {
    name: "Machine",
    nameKey: "settingsx.group.machine",
    scope: "machine",
    tabs: [
      { key: "models", label: "Models & Keys", labelKey: "settings.tab.models", icon: "code" },
      { key: "connectors", label: "Connectors", labelKey: "nav.connectors", icon: "plug" },
      { key: "context", label: "Context optimization", labelKey: "settings.tab.context", icon: "refresh" },
      { key: "skills", label: "Skills", labelKey: "settings.tab.skills", icon: "book" },
      { key: "memory", label: "Memory", labelKey: "settings.tab.memory", icon: "archive" },
      { key: "sandbox", label: "Sandbox", labelKey: "settingsx.tab.sandbox", icon: "shield" },
      { key: "personas", label: "Coworkers", labelKey: "settings.tab.personas", icon: "sparkle" },
    ],
  },
  {
    name: "Inventory",
    nameKey: "settingsx.group.inventory",
    scope: "inventory",
    tabs: [{ key: "machines", label: "Machines", labelKey: "settingsx.tab.machines", icon: "plug" }],
  },
];

export function SettingsView({
  initialTab,
  onOpenPersona,
  onCreateSkill,
  onAskWorker,
  onBack,
}: {
  initialTab?: SetTab;
  // "Back to app" on the rail — returns to the conversation surface.
  onBack?: () => void;
  onOpenPersona?: (id: string, machineId?: string | null) => void;
  // Skills doorway (SKILLS-SPEC §5.2): start a new conversation with the description
  // prefilled — the worker builds the skill and proposes it via save_skill. Under a
  // machine scope, that conversation runs ON the machine.
  onCreateSkill?: (description: string, machineId?: string | null) => void;
  // Memory's remote CTA: start a conversation ON that machine to change its memory.
  onAskWorker?: (machineId: string) => void;
}) {
  const { t } = useTranslation();
  // Personas is flag-gated (hidden for launch) — filter the tab AND coerce a stale
  // deep-link to it (openSettings("personas") callers) so the page never opens on a
  // section with no nav entry.
  const personas = showPersonas();
  // UX-049: the Slack / GitHub glance pages appear once that connector is
  // connected on any machine (the cloud's view) or on This Mac.
  const [inbound, setInbound] = useState<Set<string>>(new Set());
  useEffect(() => {
    const seen = new Set<string>();
    Promise.allSettled([
      getCloudConnections().then((rows) => {
        for (const r of rows) if (r.status !== "disconnected") seen.add(r.connector);
      }),
      getConnectors().then((cs) => {
        for (const c of cs) if (c.connected) seen.add(c.name);
      }),
    ]).then(() => setInbound(new Set([...seen].filter((n) => n === "slack" || n === "github"))));
  }, []);
  // "Manage on This Mac" from a glance page opens that connector's local
  // management page inside Connectors (one shot; the list is the default).
  const [manageDetail, setManageDetail] = useState<string | null>(null);
  const groups = SET_GROUPS.map((g) => ({
    ...g,
    tabs: g.tabs.filter(
      (tb) =>
        (personas || tb.key !== "personas") &&
        (tb.key !== "slack" && tb.key !== "github" ? true : inbound.has(tb.key)),
    ),
  }));
  const wanted = initialTab && (personas || initialTab !== "personas") ? initialTab : "appearance";
  const [tab, setTab] = useState<SetTab>(wanted);

  // The machine scope for the MACHINE group ("" = This Mac / the local engine).
  // Cloud has no local engine, so the first enrolled machine is the scope there.
  const [machines, setMachines] = useState<Machine[]>([]);
  const [scopeId, setScopeId] = useState<string>("");
  // The URL's ?m= names the scope (a deep link, or a remount of a page that
  // was already scoped). Captured at FIRST RENDER — the reflect effect below
  // rewrites the hash before the machines fetch could read it.
  const [urlScope] = useState(() => settingsMachineParam());
  useEffect(() => {
    // Union view: the picker offers local rows plus (signed-in desktop) the
    // cloud registry's, origin-tagged — scoped pages route by id prefix.
    // Reloaded whenever the fleet changes under us (a sandbox joining, a
    // machine removed) — the Machines page announces it (owner catch
    // 2026-09-02: a new sandbox needed a hard refresh to reach the picker).
    let first = true;
    const load = () =>
      Promise.all([
        getMachines().catch(() => ({ machines: [] as Machine[] })),
        isCloudMode()
          ? Promise.resolve({ machines: [] as Machine[] })
          : getCloudMachines().catch(() => ({ machines: [] as Machine[] })),
      ])
        .then(([local, cloud]) => {
          const rows = [...(local.machines ?? []), ...(cloud.machines ?? [])];
          setMachines(rows);
          // A gone machine falls back to the default scope.
          if (first && urlScope && rows.some((m) => m.id === urlScope)) setScopeId(urlScope);
          else if (isCloudMode() && rows.length) setScopeId((cur) => (cur && rows.some((m) => m.id === cur) ? cur : rows[0].id));
          first = false;
        })
        .catch(() => setMachines([]));
    void load();
    const onChange = () => void load();
    window.addEventListener(MACHINES_CHANGED, onChange);
    return () => window.removeEventListener(MACHINES_CHANGED, onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const scoped = machines.find((m) => m.id === scopeId) ?? null;

  // The open page (and its machine scope) rides the URL — #/settings/{page},
  // bookmarkable. App pages carry no scope; the fleet page is outside it.
  const machineScoped = SET_GROUPS[1].tabs.some((tb) => tb.key === tab);
  useEffect(() => {
    reflectSettings(tab, machineScoped ? scopeId || null : null);
  }, [tab, scopeId, machineScoped]);

  // The nav lives in the app's left column (App mounts #settings-rail in place of the
  // session sidebar while Settings is open) — rendered there through a portal so this
  // view keeps owning the page + scope state. Standalone mounts (tests) render it inline.
  const [railSlot, setRailSlot] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    setRailSlot(document.getElementById("settings-rail"));
  }, []);

  const nav = (
      <nav
        className={
          railSlot
            ? "settings-rail flex-1 min-h-0 overflow-y-auto hairline-scroll px-2.5 pt-2 pb-2"
            : "page-subnav w-[208px] shrink-0 border-r border-line bg-panel/40 px-3 py-4"
        }
        aria-label={t("nav.settings")}
      >
        <button
          className="settings-rail-head w-full flex items-center gap-2 px-2 py-1.5 mb-3 rounded-[7px] text-ui text-muted hover:text-ink hover:bg-chromeHover text-left"
          onClick={() => onBack?.()}
          data-testid="settings-back"
        >
          <Icon name="arrowLeft" size={15} /> {t("settingsx.back_to_app")}
        </button>
        {groups.map((g) => (
          <div key={g.name} className="mb-3.5">
            <div className="px-2 pb-1 flex items-center gap-2">
              <span className="text-label text-faint font-medium">
                {g.nameKey ? t(g.nameKey) : g.name}
              </span>
              {/* The ONE picker (UX-046): appears only when there is a choice
                  to make — extra machines on desktop, several on cloud. */}
              {g.scope === "machine" && machines.length > 0 && (
                <select
                  className="ml-auto max-w-[110px] truncate text-label px-1.5 py-0.5 rounded-md border border-line bg-paper text-ink outline-none"
                  value={scopeId}
                  onChange={(e) => setScopeId(e.target.value)}
                  data-testid="machine-scope-picker"
                  aria-label={t("settingsx.machine_scope_aria")}
                >
                  {!isCloudMode() && <option value="">{t("settingsx.this_mac_option")}</option>}
                  {machines
                    .filter((m) => m.origin !== "cloud")
                    .map((m) => (
                      <option key={m.id} value={m.id}>
                        ⌂ {m.name}
                      </option>
                    ))}
                  {machines.some((m) => m.origin === "cloud") && (
                    <optgroup label={t("settingsx.openworker_cloud")}>
                      {machines
                        .filter((m) => m.origin === "cloud")
                        .map((m) => (
                          <option key={m.id} value={m.id}>
                            ⌂ {m.name}
                          </option>
                        ))}
                    </optgroup>
                  )}
                </select>
              )}
            </div>
            {g.tabs.map((tb) => {
              const active = tab === tb.key;
              return (
                <button
                  key={tb.key}
                  className={
                    "w-full text-left px-2 py-1.5 rounded-[7px] text-ui flex items-center gap-2 " +
                    (active ? "bg-chromeHover text-ink font-medium" : "text-ink hover:bg-chromeHover")
                  }
                  onClick={() => setTab(tb.key)}
                >
                  <Icon name={tb.icon} size={15} className="text-muted" />{" "}
                  {tb.labelKey ? t(tb.labelKey) : tb.label}
                </button>
              );
            })}
          </div>
        ))}
      </nav>
  );

  return (
    <main className="flex-1 min-w-0 flex bg-paper">
      {railSlot ? createPortal(nav, railSlot) : nav}

      <div className="flex-1 min-w-0 overflow-y-auto hairline-scroll">
        <div className="max-w-3xl mx-auto px-7 py-6">
          {tab === "appearance" ? (
            <AppearanceSection />
          ) : tab === "account" ? (
            <AccountSection />
          ) : tab === "models" ? (
            scoped ? (
              <MachineModelsPanel machine={scoped} />
            ) : (
              <section>
                <PanelHead
                  title={t("settings.tab.models")}
                  sub={t("settings.models_sub")}
                />
                <ModelsTab />
              </section>
            )
          ) : tab === "context" ? (
            <section key={scopeId || "local"}>
              <PanelHead
                title={t("settings.tab.context")}
                sub={
                  scoped
                    ? t("settingsx.context.sub_machine", { name: scoped.name })
                    : t("settingsx.context.sub")
                }
              />
              <TokenSavingsCard machine={scoped} />
              <CompactionCard machine={scoped} />
            </section>
          ) : tab === "skills" ? (
            <SkillsTab
              key={scopeId || "local"}
              machine={scoped}
              onCreateSkill={(description) => onCreateSkill?.(description, scoped?.id ?? null)}
            />
          ) : tab === "voice" ? (
            <VoiceInputSection />
          ) : tab === "memory" ? (
            <MemorySection
              key={scopeId || "local"}
              machine={scoped}
              onAskWorker={(machineId) => onAskWorker?.(machineId)}
            />
          ) : tab === "sandbox" ? (
            <SandboxSection key={scopeId || "local"} machine={scoped} />
          ) : tab === "machines" ? (
            <MachinesSection />
          ) : tab === "slack" || tab === "github" ? (
            <ConnectorGlance
              key={tab}
              connector={tab}
              onManage={(name) => {
                setManageDetail(name);
                setScopeId("");
                setTab("connectors");
              }}
            />
          ) : tab === "connectors" ? (
            // Connectors moved here from its own surface (owner 2026-08-31):
            // This-Mac scope = the full existing page; a machine scope = that
            // machine's list with the flows a headless box can run (sealed
            // manual connect; browser sign-ins stay on the controller).
            <section>
              <PanelHead
                title={t("nav.connectors")}
                sub={
                  scoped
                    ? t("settingsx.connectors.sub_machine", { name: scoped.name })
                    : t("integrations.connectors_sub")
                }
              />
              {scoped ? (
                <RemoteConnectorsPanel
                  key={scopeId}
                  machine={scoped}
                  onConfigure={(name) => setTab(name === "github" ? "github" : "slack")}
                />
              ) : (
                <ConnectorsSection
                  key={manageDetail || "list"}
                  initialDetail={manageDetail}
                  onConfigure={(name) => {
                    setManageDetail(null);
                    setTab(name === "github" ? "github" : "slack");
                  }}
                />
              )}
            </section>
          ) : (
            <PersonasSection
              key={scopeId || "local"}
              machine={scoped}
              onOpenPersona={(id) => onOpenPersona?.(id, scoped?.id ?? null)}
            />
          )}
        </div>
      </div>
    </main>
  );
}

// -- Voice input: deliberate model provisioning + compatibility + microphone test (§37) --------
const voiceError = (error: unknown) =>
  error instanceof Error
    ? error.message
    : typeof error === "string"
      ? error
      : getI18n().getFixedT(null, "translation")("settings.voice_action_failed");

const formatBytes = (bytes: number) => {
  if (!bytes) return "0 MiB";
  return `${Math.round(bytes / 1024 / 1024)} MiB`;
};

function VoiceInputSection() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<DictationStatus | null>(null);
  const [progress, setProgress] = useState<DictationDownloadProgress | null>(null);
  const [phase, setPhase] = useState<"idle" | "downloading" | "verifying" | "testing" | "transcribing">("idle");
  const [error, setError] = useState<string | null>(null);
  const [testTranscript, setTestTranscript] = useState("");
  const desktop = isTauri();

  const publish = (next: DictationStatus) => {
    setStatus(next);
    window.dispatchEvent(new CustomEvent("coworker:voice-input-changed", { detail: next }));
  };

  useEffect(() => {
    if (!desktop) return;
    let active = true;
    let unlisten = () => {};
    void listenDictationDownloadProgress((next) => {
      if (active) setProgress(next);
    }).then((stop) => {
      unlisten = stop;
    });
    void getDictationStatus().then(async (initial) => {
      if (!active || !initial) return;
      publish(initial);
      // One-time migration for models installed by the first STT cut, before verification markers.
      if (initial.model_installed && !initial.model_verified) {
        setPhase("verifying");
        try {
          const verified = await verifyDictationModel();
          if (active) publish(verified);
        } catch (verifyError) {
          if (active) setError(voiceError(verifyError));
        } finally {
          if (active) setPhase("idle");
        }
      }
    });
    return () => {
      active = false;
      unlisten();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop]);

  const download = async () => {
    setError(null);
    setProgress({ downloaded_bytes: 0, total_bytes: status?.model_bytes || 0 });
    setPhase("downloading");
    try {
      publish(await downloadDictationModel());
    } catch (downloadError) {
      setError(voiceError(downloadError));
      const latest = await getDictationStatus();
      if (latest) publish(latest);
    } finally {
      setPhase("idle");
    }
  };

  const cancelDownload = async () => {
    await cancelDictationModelDownload().catch(() => undefined);
  };

  const repair = async () => {
    setError(null);
    try {
      publish(await deleteDictationModel());
      await download();
    } catch (repairError) {
      setError(voiceError(repairError));
    }
  };

  const remove = async () => {
    if (!window.confirm(t("settings.voice_delete_confirm"))) return;
    setError(null);
    try {
      publish(await deleteDictationModel());
      setTestTranscript("");
      setProgress(null);
    } catch (deleteError) {
      setError(voiceError(deleteError));
    }
  };

  const toggleTest = async () => {
    if (!status?.supported || !status.model_verified) return;
    setError(null);
    try {
      if (status.recording) {
        setPhase("transcribing");
        const transcript = (await stopDictation()).trim();
        setTestTranscript(transcript);
        if (!transcript) throw new Error(t("settings.voice_no_speech"));
        publish(await markDictationTestPassed());
      } else {
        setTestTranscript("");
        setPhase("testing");
        publish(await startDictation());
      }
    } catch (testError) {
      setError(voiceError(testError));
      const latest = await getDictationStatus();
      if (latest) publish(latest);
    } finally {
      setPhase("idle");
    }
  };

  const downloading = phase === "downloading" || !!status?.download_in_progress;
  const progressTotal = progress?.total_bytes || status?.model_bytes || 1;
  const progressPercent = Math.min(100, Math.round(((progress?.downloaded_bytes || 0) / progressTotal) * 100));
  const ready = !!status?.supported && !!status?.model_verified && !!status?.test_passed;

  return (
    <section>
      <PanelHead
        title={t("settings.tab.voice")}
        sub={t("settings.voice_intro")}
      />

      {!desktop ? (
        <div className={CARD + " p-4 text-ui text-muted"}>{t("settings.voice_desktop_only")}</div>
      ) : (
        <div className="space-y-4">
          <div className="rounded-xl border border-green-200 bg-green-50/70 px-4 py-3 text-ui text-green-800">
            <span className="font-medium">{t("settings.voice_private_title")}</span>{" "}
            {t("settings.voice_private_body")}
          </div>

          <div className={CARD}>
            <div className="p-4 flex items-start gap-3">
              <Icon name="code" size={18} className="text-accent mt-0.5" />
              <div className="min-w-0 flex-1">
                <div className="text-ui font-medium">{t("settings.voice_device_title")}</div>
                <div className="text-meta text-muted mt-1">{status?.device_summary || t("settings.voice_checking")}</div>
                {status?.compatibility_reason && <div className="text-meta text-red-600 mt-1.5">{status.compatibility_reason}</div>}
              </div>
              {status && (
                <span className={"text-meta px-2 py-1 rounded-full " + (status.supported ? "bg-green-50 text-green-700" : "bg-red-50 text-red-600")}>
                  {status.supported ? `● ${t("settings.voice_compatible")}` : t("settings.voice_unsupported")}
                </span>
              )}
            </div>
            <div className="border-t border-line bg-paper/50 px-4 py-3 grid grid-cols-2 gap-3 text-meta text-muted">
              <div><span className="block text-ink font-medium">{t("settings.voice_mac")}</span>{t("settings.voice_mac_detail")}</div>
              <div><span className="block text-ink font-medium">{t("settings.voice_windows")}</span>{t("settings.voice_windows_detail")}</div>
              <div><span className="block text-ink font-medium">{t("settings.voice_memory")}</span>{t("settings.voice_memory_detail")}</div>
              <div><span className="block text-ink font-medium">{t("settings.voice_processor")}</span>{t("settings.voice_processor_detail")}</div>
            </div>
          </div>

          <div className={CARD}>
            <div className="p-4 flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-accentSoft text-accent grid place-items-center font-semibold">W</div>
              <div className="min-w-0 flex-1">
                <div className="text-ui font-medium">{t("settings.voice_whisper_title")}</div>
                <div className="text-meta text-muted mt-0.5">
                  {status?.model_verified
                    ? t("settings.voice_installed", { size: formatBytes(status.model_bytes) })
                    : t("settings.voice_not_installed", { size: formatBytes(status?.model_bytes || 147_964_211) })}
                </div>
              </div>
              {status?.model_verified ? (
                <>
                  <span className="text-meta px-2 py-1 rounded-full bg-green-50 text-green-700">{t("settings.voice_verified")}</span>
                  <button className={BTN_BORDERED} onClick={() => void repair()}>{t("settings.voice_repair")}</button>
                  <button className="text-meta text-red-600 px-2 py-2" onClick={() => void remove()}>{t("settings.voice_delete")}</button>
                </>
              ) : downloading ? (
                <button className={BTN_BORDERED} onClick={() => void cancelDownload()}>{t("common.stop")}</button>
              ) : phase === "verifying" ? (
                <span className="text-meta text-muted">{t("settings.voice_verifying")}</span>
              ) : (
                <button className={BTN_ACCENT} disabled={!status?.supported} onClick={() => void download()}>{t("settings.voice_download")}</button>
              )}
            </div>
            {downloading && (
              <div className="border-t border-line px-4 py-3">
                <div className="h-1.5 rounded-full bg-line overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: `${progressPercent}%` }} /></div>
                <div className="mt-1.5 text-meta text-muted flex"><span>{t("settings.voice_dl_progress", { done: formatBytes(progress?.downloaded_bytes || 0), total: formatBytes(progressTotal) })}</span><span className="ml-auto">{progressPercent}%</span></div>
              </div>
            )}
          </div>

          <div className={CARD}>
            <div className="p-4 flex items-center gap-3">
              <Icon name="mic" size={18} className={ready ? "text-green-600" : "text-muted"} />
              <div className="min-w-0 flex-1">
                <div className="text-ui font-medium">{t("settings.voice_mic_test_title")}</div>
                <div className="text-meta text-muted mt-0.5">
                  {ready ? t("settings.voice_mic_test_ready") : t("settings.voice_mic_test_pending")}
                </div>
              </div>
              {ready && <span className="text-meta px-2 py-1 rounded-full bg-green-50 text-green-700">● {t("settings.voice_ready_badge")}</span>}
              <button className={BTN_BORDERED} disabled={!status?.supported || !status?.model_verified || phase === "transcribing"} onClick={() => void toggleTest()}>
                {status?.recording
                  ? t("settings.voice_stop_check")
                  : phase === "transcribing"
                    ? t("settings.voice_transcribing")
                    : ready
                      ? t("settings.voice_test_again")
                      : t("settings.voice_test_mic")}
              </button>
            </div>
            {status?.recording && <div className="border-t border-line px-4 py-3 text-meta text-accent" role="status">{t("settings.voice_listening")}</div>}
            {testTranscript && <div className="border-t border-line bg-paper/50 px-4 py-3 text-ui">“{testTranscript}”</div>}
          </div>

          {error && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-meta text-red-700">{error}</div>}
        </div>
      )}
    </section>
  );
}

// -- Personas: installed/enabled/delete management, the dir/Git importer, and the
// entry point to the Persona Gallery (a screen-sized modal — installs finish back
// here, disabled pending consent; a gallery install re-mounts the list in place).
// The Gallery entry point is GONE (owner 2026-08-21) — coworkers install from
// GitHub / folder / zip only. GalleryModal stays in the tree for the gallery's
// possible return as a first-class distribution surface, but nothing mounts it.
function PersonasSection({
  onOpenPersona,
  machine,
}: {
  onOpenPersona?: (id: string) => void;
  machine?: Machine | null;
}) {
  const { t } = useTranslation();
  return (
    <section>
      <PanelHead
        title={t("settings.tab.personas")}
        sub={
          machine
            ? t("settingsx.personas.machine_sub", { name: machine.name })
            : t("settings.personas_intro")
        }
      />
      <p className="text-ui text-muted leading-relaxed max-w-[560px] mt-5 mb-1">
        {t("settings.personas_desc")}
      </p>
      <PersonasTab onOpenPersona={onOpenPersona} machine={machine} />
    </section>
  );
}

// -- Account -------------------------------------------------------------------
// Two deployments, two meanings of "account". On the hosted dashboard the page
// is the signed-in identity: who you are, the organization this browser acts
// in, and the way out (moved here from the Machines page's inline bar). On
// desktop and self-hosted browsers it is the optional OpenWorker Cloud
// sign-in — the same one the sidebar's account menu offers, given a full page.
// Settings ▸ Sandbox (UX-051 A, design doc rulings 17, 29, 32): machine-level provider,
// network allow list, and the credential files an agent may be given. Reads and writes the
// machine's config.toml through /v1/settings/sandbox; nothing here is per project.
function SandboxSection({ machine }: { machine?: Machine | null }) {
  const { t } = useTranslation();
  const mid = machine?.id ?? null;
  const [cfg, setCfg] = useState<SandboxSettings | null>(null);
  const [error, setError] = useState<string>("");
  const [editing, setEditing] = useState<string | null>(null); // credential name being edited, "" = new

  useEffect(() => {
    getSandboxSettings(mid).then(setCfg).catch(() => setCfg(null));
  }, [mid]);

  const save = async (patch: Parameters<typeof setSandboxSettings>[0]) => {
    const res = await setSandboxSettings(patch, mid);
    if (!res.ok) {
      setError(res.error || "could not save");
      return;
    }
    setError("");
    setCfg(res as SandboxSettings);
  };

  if (!cfg) return null;
  const providerNames: Record<string, [string, string]> = {
    direct: [t("settingsx.sandbox.provider_direct"), t("settingsx.sandbox.provider_direct_desc")],
    seatbelt: [t("settingsx.sandbox.provider_seatbelt"), t("settingsx.sandbox.provider_seatbelt_desc")],
    openshell: [t("settingsx.sandbox.provider_openshell"), t("settingsx.sandbox.provider_openshell_desc")],
  };
  const chosen = cfg.provider || cfg.effective_provider || "direct";
  const shipped = (name: string, key: "title" | "does", fallback?: string) => {
    const k = `settingsx.sandbox.${key}_${name}`;
    const v = t(k);
    return v === k ? fallback || "" : v;
  };
  const updateCredentials = (rows: SandboxCredentialEntry[]) => save({ credentials: rows });

  return (
    <section data-testid="sandbox-section">
      <PanelHead
        title={t("settingsx.sandbox.title")}
        sub={machine ? t("settingsx.sandbox.sub_machine", { name: machine.name }) : t("settingsx.sandbox.sub")}
      />
      {error ? <div className="mb-3 text-meta text-danger">{error}</div> : null}

      <div className={FIELD_LABEL + " mb-2"}>{t("settingsx.sandbox.run_in")}</div>
      <div className={CARD + " mb-1 divide-y divide-line"} role="radiogroup" aria-label={t("settingsx.sandbox.run_in")}>
        {cfg.providers
          .filter((p) => p.name !== "seatbelt" || cfg.platform === "darwin")
          .map((p) => {
            const [label, desc] = providerNames[p.name] ?? [p.name, ""];
            const active = chosen === p.name;
            return (
              <label key={p.name} className="flex items-start gap-3 px-4 py-3 cursor-pointer">
                <input
                  type="radio"
                  name="sandbox-provider"
                  className="mt-1"
                  checked={active}
                  disabled={!p.usable && !active}
                  onChange={() => save({ provider: p.name === "direct" && !cfg.provider ? "" : p.name })}
                  data-testid={`sandbox-provider-${p.name}`}
                />
                <span className="flex-1 min-w-0">
                  <span className="block text-ui text-ink">{label}</span>
                  <span className="block text-meta text-muted">{desc}</span>
                </span>
                <span className={"text-meta shrink-0 " + (p.usable ? (active ? "text-success" : "text-muted") : "text-muted")} title={p.why || undefined}>
                  {p.name === "direct" && !cfg.provider
                    ? t("settingsx.sandbox.status_default")
                    : p.usable
                      ? t("settingsx.sandbox.status_ready")
                      : t("settingsx.sandbox.status_unavailable")}
                </span>
              </label>
            );
          })}
      </div>
      {cfg.refused ? <div className="mb-4 text-meta text-danger">{t("settingsx.sandbox.refused", { why: cfg.refused })}</div> : <div className="mb-4" />}

      <div className={FIELD_LABEL + " mb-2"}>{t("settingsx.sandbox.network")}</div>
      <div className={CARD + " divide-y divide-line"} role="radiogroup" aria-label={t("settingsx.sandbox.network")}>
        {cfg.network_profiles.map((np) => (
          <label key={np.name} className="flex items-start gap-3 px-4 py-3 cursor-pointer">
            <input
              type="radio"
              name="sandbox-network"
              className="mt-1"
              checked={cfg.network_profile === np.name}
              onChange={() => save({ network_profile: np.name })}
              data-testid={`sandbox-network-${np.name}`}
            />
            <span className="flex-1 min-w-0">
              <span className="block text-ui text-ink">{t(`settingsx.sandbox.profile_${np.name}`)}</span>
              <span className="block text-meta text-muted">{t(`settingsx.sandbox.profile_${np.name}_desc`)}</span>
            </span>
          </label>
        ))}
      </div>
      <div className={FIELD_HELP + " mb-4"}>{t("settingsx.sandbox.network_help")}</div>

      <div className="flex items-center mb-2">
        <div className={FIELD_LABEL}>{t("settingsx.sandbox.credentials")}</div>
        <button className="ml-auto text-ui text-accent" onClick={() => setEditing("")} data-testid="sandbox-credential-add">
          {t("settingsx.sandbox.add")}
        </button>
      </div>
      <div className={CARD + " divide-y divide-line"}>
        {cfg.credentials.map((c) => (
          <div key={c.name} className="flex items-start gap-3 px-4 py-3" data-testid={`sandbox-credential-${c.name}`}>
            <input
              type="checkbox"
              className="mt-1"
              checked={c.enabled}
              onChange={(e) => updateCredentials(cfg.credentials.map((x) => (x.name === c.name ? { ...x, enabled: e.target.checked } : x)))}
              aria-label={c.title || shipped(c.name, "title", c.name)}
            />
            <span className="flex-1 min-w-0">
              <span className="block text-ui text-ink">
                {c.title || shipped(c.name, "title", c.name)}{" "}
                <code className="text-meta text-muted font-mono">{c.path}</code>
              </span>
              <span className="block text-meta text-muted">
                {c.does ? c.does : shipped(c.name, "does")}{" "}
                {c.hosts && c.hosts.length ? <span className="text-faint">{t("settingsx.sandbox.also_allows", { hosts: c.hosts.join(", ") })}</span> : null}
              </span>
            </span>
            <span className="text-meta text-muted shrink-0 whitespace-nowrap">
              <button className="hover:text-ink" onClick={() => setEditing(c.name)}>{t("settingsx.sandbox.edit")}</button>
              <span className="mx-1.5 text-faint">·</span>
              <button className="hover:text-ink" onClick={() => updateCredentials(cfg.credentials.filter((x) => x.name !== c.name))}>
                {t("settingsx.sandbox.remove")}
              </button>
            </span>
          </div>
        ))}
      </div>
      <div className={FIELD_HELP}>{t("settingsx.sandbox.credentials_help")}</div>
      {cfg.platform === "darwin" ? <div className={FIELD_HELP}>{t("settingsx.sandbox.keychain_note")}</div> : null}
      <div className={FIELD_HELP + " mt-3"}>{t("settingsx.sandbox.saved_in", { path: cfg.config_path })}</div>

      {editing !== null ? (
        <CredentialEditor
          entry={editing ? cfg.credentials.find((c) => c.name === editing) ?? null : null}
          onCancel={() => setEditing(null)}
          onSave={(row) => {
            const rows = editing ? cfg.credentials.map((x) => (x.name === editing ? row : x)) : [...cfg.credentials, row];
            setEditing(null);
            updateCredentials(rows);
          }}
        />
      ) : null}
    </section>
  );
}

function CredentialEditor({
  entry,
  onCancel,
  onSave,
}: {
  entry: SandboxCredentialEntry | null;
  onCancel: () => void;
  onSave: (row: SandboxCredentialEntry) => void;
}) {
  const { t } = useTranslation();
  const [title, setTitle] = useState(entry?.title ?? "");
  const [name, setName] = useState(entry?.name ?? "");
  const [path, setPath] = useState(entry?.path ?? "~/");
  const [hosts, setHosts] = useState((entry?.hosts ?? []).join("\n"));
  const [does, setDoes] = useState(entry?.does ?? "");
  const slug = entry?.name || name || title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const valid = Boolean(slug) && (path.startsWith("~/") || path.startsWith("/"));
  return (
    <div className={CARD + " p-4 mt-3"} data-testid="sandbox-credential-editor">
      <div className="grid grid-cols-[150px_1fr] gap-x-3 gap-y-2 items-center">
        <label className="text-ui text-muted">{t("settingsx.sandbox.field_title")}</label>
        <input className={INPUT} value={title} onChange={(e) => { setTitle(e.target.value); if (!entry) setName(""); }} />
        <label className="text-ui text-muted">{t("settingsx.sandbox.field_path")}</label>
        <input className={INPUT + " font-mono"} value={path} onChange={(e) => setPath(e.target.value)} />
        <label className="text-ui text-muted self-start pt-2">{t("settingsx.sandbox.field_hosts")}</label>
        <textarea className={INPUT + " font-mono"} rows={3} value={hosts} onChange={(e) => setHosts(e.target.value)} />
        <label className="text-ui text-muted">{t("settingsx.sandbox.field_does")}</label>
        <input className={INPUT} value={does} onChange={(e) => setDoes(e.target.value)} />
      </div>
      <div className="flex justify-end gap-2 mt-3">
        <button className={BTN_BORDERED} onClick={onCancel}>{t("settingsx.sandbox.cancel")}</button>
        <button
          className={BTN_ACCENT}
          disabled={!valid}
          onClick={() =>
            onSave({
              name: slug,
              title: title || undefined,
              path,
              hosts: hosts.split(/\s+/).map((h) => h.trim()).filter(Boolean),
              does: does || undefined,
              enabled: entry?.enabled ?? true,
            })
          }
        >
          {t("settingsx.sandbox.done")}
        </button>
      </div>
    </div>
  );
}

function AccountSection() {
  const { t } = useTranslation();
  return (
    <section>
      <PanelHead
        title={t("settingsx.tab.account")}
        sub={
          isCloudMode()
            ? t("settingsx.account.sub_hosted")
            : t("settingsx.account.sub_desktop")
        }
      />
      {isCloudMode() ? <HostedAccountCards /> : <DesktopCloudCard />}
    </section>
  );
}

function HostedAccountCards() {
  const { t } = useTranslation();
  // Fetched here rather than read from the boot gate: the page should show
  // the identity as the API resolves it right now, under the active org.
  const [me, setMe] = useState<MeInfo | null | undefined>(undefined);
  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  if (me === undefined) return <div className="text-meta text-muted mt-3">{t("settingsx.account.loading")}</div>;
  if (me === null)
    return (
      <div className={CARD + " p-4 text-ui text-muted"}>
        {t("settingsx.account.no_signin_service")}
      </div>
    );
  const active = getActiveOrg() || me.org_id;
  const activeOrg = me.orgs.find((o) => o.id === active);
  return (
    <div data-testid="cloud-account-card">
      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>{t("settingsx.account.signed_in_as")}</div>
        <div className="mt-2 flex items-center gap-3">
          <span className="w-8 h-8 rounded-full bg-accentSoft text-accent grid place-items-center text-ui font-semibold">
            {(me.actor || "?").slice(0, 1).toUpperCase()}
          </span>
          <span className="text-ui text-ink min-w-0 truncate">{me.actor}</span>
          {canSignOut() && (
            <button
              className={BTN_BORDERED + " ml-auto"}
              onClick={() => void cloudSignOut()}
            >
              {t("sidebar.sign_out")}
            </button>
          )}
        </div>
      </div>

      {/* One login, one org (spec 2026-08-31): the org is a function of the
          identity — display only, never a choice made here. */}
      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>{t("settingsx.account.organization")}</div>
        <div className="mt-2 text-ui text-ink" data-testid="org-name">
          {activeOrg?.name ?? active}
        </div>
        <div className={FIELD_HELP}>
          {t("settingsx.account.organization_help")}
        </div>
      </div>
    </div>
  );
}

function DesktopCloudCard() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const [waiting, setWaiting] = useState(false);
  const cancelWait = useRef<(() => void) | null>(null);

  useEffect(() => {
    const load = () =>
      getCloudStatus()
        .then(setStatus)
        .catch(() => setStatus({ signed_in: false, account: "", user_id: "" }));
    load();
    window.addEventListener(CLOUD_CHANGED, load);
    return () => {
      window.removeEventListener(CLOUD_CHANGED, load);
      cancelWait.current?.();
    };
  }, []);

  const signIn = async () => {
    setWaiting(true);
    await cloudLogin().catch(() => {});
    cancelWait.current?.();
    cancelWait.current = waitForCloudSignIn((s) => {
      setWaiting(false);
      if (s) setStatus(s);
      if (s?.signed_in) announceCloudChanged();
    });
  };
  const signOut = async () => {
    await cloudLogout().catch(() => {});
    announceCloudChanged();
  };

  return (
    <div className={CARD + " p-4"} data-testid="cloud-signin-card">
      <div className={FIELD_LABEL}>{t("settingsx.openworker_cloud")}</div>
      {status === null ? (
        <div className="text-meta text-muted mt-2">{t("settingsx.account.checking_signin")}</div>
      ) : status.signed_in ? (
        <div className="mt-2 flex items-center gap-3">
          <span className="w-8 h-8 rounded-full bg-accentSoft text-accent grid place-items-center text-ui font-semibold">
            {(status.account || "?").slice(0, 1).toUpperCase()}
          </span>
          <span className="text-ui text-ink min-w-0 truncate">{status.account}</span>
          <button className={BTN_BORDERED + " ml-auto"} onClick={() => void signOut()}>
            {t("sidebar.sign_out")}
          </button>
        </div>
      ) : (
        <>
          <div className={FIELD_HELP}>
            {t("settingsx.account.signin_help")}
          </div>
          <button
            className={BTN_ACCENT + " mt-3"}
            onClick={() => void signIn()}
            disabled={waiting}
            data-testid="account-page-sign-in"
          >
            {waiting ? t("cloud.check_browser") : t("cloud.sign_in")}
          </button>
        </>
      )}
    </div>
  );
}

// -- Appearance + app behaviour ------------------------------------------------
function AppearanceSection() {
  const { t } = useTranslation();
  const [theme, setTheme] = useThemePref();
  const [textSize, setTextSizePref] = useTextSize();
  const [autostart, setAuto] = useState(false);
  const [keepAwake, setKeep] = useState(false);
  const desktop = isTauri();
  // "system" = no explicit choice persisted; the app follows the OS locale.
  const [currentLang, setCurrentLang] = useState<Lang | "system">(() => getStoredLanguage() ?? "system");

  useEffect(() => {
    if (isTauri()) {
      getAutostart().then((v) => setAuto(!!v));
      getKeepAwake().then((v) => setKeep(!!v));
    }
  }, []);

  const toggleAuto = async (v: boolean) => setAuto(!!(await setAutostart(v)));
  const toggleKeep = async (v: boolean) => setKeep(!!(await setKeepAwake(v)));
  const runSetupAgain = async () => {
    await setOnboarded(false);
    window.dispatchEvent(new CustomEvent("coworker:open-onboarding"));
  };
  const changeLang = (lang: Lang | "system") => {
    setCurrentLang(lang);
    void setI18nLanguage(lang === "system" ? null : lang);
  };

  return (
    <section>
      <PanelHead title={t("settings.general_title")} sub={t("settings.general_sub")} />

      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>{t("settings.theme")}</div>
        <div className="seg mt-2.5" role="radiogroup" aria-label={t("settings.appearance_aria")}>
          {(["light", "dark", "auto"] as const).map((p) => (
            <button key={p} className={p === theme ? "active" : ""} onClick={() => setTheme(p)}>
              {p === "light" ? t("settings.theme_light") : p === "dark" ? t("settings.theme_dark") : t("settings.theme_auto")}
            </button>
          ))}
        </div>
        <div className={FIELD_HELP}>{t("settings.theme_auto_help")}</div>
      </div>

      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>{t("settings.language")}</div>
        <div className="seg mt-2.5" role="radiogroup" aria-label={t("settings.language_aria")}>
          {(["system", "en", "zh"] as const).map((lng) => (
            <button
              key={lng}
              className={lng === currentLang ? "active" : ""}
              onClick={() => changeLang(lng)}
            >
              {lng === "zh" ? t("settings.language_zh") : lng === "en" ? t("settings.language_en") : t("settings.language_system")}
            </button>
          ))}
        </div>
        <div className={FIELD_HELP}>{t("settings.language_help")}</div>
      </div>

      <div className={CARD + " p-4 mb-4"} data-testid="text-size-card">
        <div className={FIELD_LABEL}>{t("settingsx.text_size.title")}</div>
        <div className="seg mt-2.5" role="radiogroup" aria-label={t("settingsx.text_size.title")}>
          {(["small", "default", "large"] as const).map((s) => (
            <button
              key={s}
              className={s === textSize ? "active" : ""}
              onClick={() => setTextSizePref(s)}
              data-testid={`text-size-${s}`}
              aria-checked={s === textSize}
              role="radio"
            >
              {s === "small"
                ? t("settingsx.text_size.small")
                : s === "default"
                  ? t("settingsx.text_size.default")
                  : t("settingsx.text_size.large")}
            </button>
          ))}
        </div>
        <div className={FIELD_HELP}>{t("settingsx.text_size.help")}</div>
      </div>

      <SidebarCard />

      <ContextBarCard />

      <AutoApproveCard />

      <FilesCard />

      <TrustedWorkspacesCard />

      {desktop && (
        <div className={CARD + " p-4"}>
          <div className={FIELD_LABEL + " mb-2.5"}>{t("settings.always_on")}</div>
          <label className="flex items-start gap-3 py-2">
            <input type="checkbox" className="mt-0.5" checked={autostart} onChange={(e) => toggleAuto(e.target.checked)} />
            <span>
              <span className="block text-ui text-ink">{t("settings.open_at_login")}</span>
              <span className="block text-meta text-muted">{t("settings.open_at_login_help")}</span>
            </span>
          </label>
          <label className="flex items-start gap-3 py-2">
            <input type="checkbox" className="mt-0.5" checked={keepAwake} onChange={(e) => toggleKeep(e.target.checked)} />
            <span>
              <span className="block text-ui text-ink">{t("settings.keep_awake")}</span>
              <span className="block text-meta text-muted">{t("settings.keep_awake_help")}</span>
            </span>
          </label>
        </div>
      )}

      {/* One card for the app-lifecycle actions (UX-021): the onboarding replay (§24 —
          every build, the browser dev shell runs the same first-run flow) and, on
          desktop, the manual update check (launch also checks automatically). */}
      <div className={CARD + " p-4 mt-4"}>
        <div className={FIELD_LABEL + " mb-2"}>{t("settings.setup_updates")}</div>
        <div className="flex items-center gap-2">
          <button className={BTN_BORDERED} onClick={runSetupAgain}>
            {t("settings.run_setup_again")}
          </button>
          {desktop && <UpdateInline />}
        </div>
        <div className={FIELD_HELP}>{t("settings.run_setup_help")}</div>
      </div>
    </section>
  );
}

function TrustedWorkspacesCard() {
  const { t } = useTranslation();
  const [workspaces, setWorkspaces] = useState<WorkspaceCommandTrust[] | null>(null);

  const refresh = () =>
    getTrustedWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspaces([]));

  useEffect(() => {
    refresh();
  }, []);

  const revoke = async (path: string) => {
    if (!window.confirm(t("settings.trust_revoke_confirm", { path }))) return;
    await setWorkspaceTrusted(path, false);
    refresh();
  };

  return (
    <div className={CARD + " p-4 mb-4"} data-testid="trusted-workspaces-card">
      <div className={FIELD_LABEL}>{t("settings.trusted_workspaces")}</div>
      <div className={FIELD_HELP}>
        {t("settings.trusted_workspaces_help")}
      </div>
      {workspaces === null ? (
        <div className="text-meta text-muted mt-3">{t("settings.trust_loading")}</div>
      ) : workspaces.length === 0 ? (
        <div className="text-meta text-muted mt-3">{t("settings.trust_empty")}</div>
      ) : (
        <div className="mt-3 divide-y divide-line">
          {workspaces.map((workspace) => (
            <div key={workspace.workspace} className="py-2.5 flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-ui text-ink break-all">{workspace.workspace}</div>
                <div className="text-meta text-muted mt-0.5">
                  {workspace.requested_commands.length
                    ? t("settings.trust_allowances", { count: workspace.requested_commands.length })
                    : t("settings.trust_no_allowances")}
                  {!workspace.exists ? t("settings.trust_folder_unavailable") : ""}
                </div>
              </div>
              <button
                className="text-meta text-red-600 px-2 py-1"
                onClick={() => void revoke(workspace.workspace)}
              >
                {t("settings.trust_revoke")}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function UpdateInline() {
  const { t } = useTranslation();
  const [state, setState] = useState<"idle" | "checking" | "none" | "found" | "installing" | "error">("idle");
  const [version, setVersion] = useState("");

  const check = async () => {
    setState("checking");
    try {
      const u = await checkForUpdate();
      if (u) {
        setVersion(u.version);
        setState("found");
      } else {
        setState("none");
      }
    } catch {
      setState("error");
    }
  };

  const install = async () => {
    setState("installing");
    try {
      await installUpdate(); // success restarts the app
    } catch {
      setState("error");
    }
  };

  return (
    <span className="inline-flex items-center gap-2.5">
      {state === "found" ? (
        <button className={BTN_BORDERED} onClick={install} data-testid="settings-update-install">
          {t("settings.update_install", { version })}
        </button>
      ) : (
        <button
          className={BTN_BORDERED}
          onClick={check}
          disabled={state === "checking" || state === "installing"}
          data-testid="settings-update-check"
        >
          {state === "checking" ? t("settings.checking") : t("settings.check_for_updates")}
        </button>
      )}
      {(state === "none" || state === "error" || state === "installing") && (
        <span className="text-meta text-muted">
          {state === "none"
            ? t("settings.update_latest")
            : state === "error"
              ? t("settings.update_error")
              : t("settings.update_downloading")}
        </span>
      )}
    </span>
  );
}

// Telemetry/Privacy card removed for this release (owner ask 2026-07-22); the
// setCloudTelemetry API stays for a future opt-out surface.

// -- Sidebar density -------------------------------------------------------------
// -- Token savings (PDF attachments; owner ask, 2026-07-17) ---------------------
// Attachments replay with EVERY turn, so a big PDF quietly multiplies token spend.
// This card is the attachment dial: attach thresholds + the fallback for models
// without native PDF support. (Long-history spend is handled by auto-compaction —
// the CompactionCard below, OPE-27.)
function TokenSavingsCard({ machine }: { machine?: Machine | null }) {
  const { t } = useTranslation();
  const mid = machine?.id ?? null;
  const [pdf, setPdf] = useState<PdfSettings | null>(null);

  useEffect(() => {
    getSettings(mid)
      .then((s) =>
        setPdf({
          pdf_fallback: s.pdf_fallback || "text",
          pdf_max_pages: s.pdf_max_pages || 20,
          pdf_max_mb: s.pdf_max_mb || 10,
        }),
      )
      .catch(() => setPdf({ pdf_fallback: "text", pdf_max_pages: 20, pdf_max_mb: 10 }));
  }, [mid]);

  const save = async (patch: Partial<PdfSettings>) => {
    setPdf((p) => (p ? { ...p, ...patch } : p));
    await setPdfSettings(patch, mid);
  };

  if (!pdf) return null;
  return (
    <div className={CARD + " p-4 mb-4"} data-testid="token-savings-card">
      <div className={FIELD_LABEL}>{t("settings.token_savings")}</div>
      <div className={FIELD_HELP}>
        {t("settings.token_savings_help")}
      </div>

      <div className="mt-3 text-ui text-ink">{t("settings.pdf_fallback_label")}</div>
      <div className="seg mt-2" role="radiogroup" aria-label={t("settings.pdf_fallback_aria")} data-testid="pdf-fallback">
        <button
          className={pdf.pdf_fallback === "text" ? "active" : ""}
          onClick={() => save({ pdf_fallback: "text" })}
        >
          {t("settings.pdf_extract_text")}
        </button>
        <button
          className={pdf.pdf_fallback === "images" ? "active" : ""}
          onClick={() => save({ pdf_fallback: "images" })}
        >
          {t("settings.pdf_send_images")}
        </button>
      </div>
      <div className={FIELD_HELP}>
        {t("settings.pdf_fallback_help")}
      </div>

      <div className="mt-3 flex items-center gap-5">
        <label className="flex items-center gap-2.5">
          <span className="text-ui text-ink">{t("settings.pdf_max_pages")}</span>
          <input
            type="number"
            min={1}
            max={100}
            value={pdf.pdf_max_pages}
            data-testid="pdf-max-pages"
            className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
            onChange={(e) => save({ pdf_max_pages: Math.max(1, Math.min(Number(e.target.value) || 20, 100)) })}
          />
        </label>
        <label className="flex items-center gap-2.5">
          <span className="text-ui text-ink">{t("settings.pdf_max_size")}</span>
          <input
            type="number"
            min={1}
            max={10}
            value={pdf.pdf_max_mb}
            data-testid="pdf-max-mb"
            className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
            onChange={(e) => save({ pdf_max_mb: Math.max(1, Math.min(Number(e.target.value) || 10, 10)) })}
          />
          <span className="text-ui text-muted">MB</span>
        </label>
      </div>
      <div className={FIELD_HELP}>
        {t("settings.pdf_limits_help")}
      </div>
    </div>
  );
}

// -- Context compaction (OPE-27) ------------------------------------------------
// Long sessions are summarized automatically when they approach the model's context
// limit, so work continues instead of hitting a raw provider error. Two spec'd
// overrides (trigger % + token cap) and the summarizer-model pin — nothing more.
function CompactionCard({ machine }: { machine?: Machine | null }) {
  const { t } = useTranslation();
  const mid = machine?.id ?? null;
  const [cfg, setCfg] = useState<CompactionSettings | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});

  useEffect(() => {
    getSettings(mid)
      .then((s) => {
        setCfg({
          compaction_threshold_pct: s.compaction_threshold_pct ?? 0.8,
          compaction_cap_tokens: s.compaction_cap_tokens ?? 250_000,
          compaction_model: s.compaction_model ?? "",
        });
        setModels(s.models || []);
        setLabels(s.model_labels || {});
      })
      .catch(() =>
        setCfg({
          compaction_threshold_pct: 0.8,
          compaction_cap_tokens: 250_000,
          compaction_model: "",
        }),
      );
  }, [mid]);

  const save = async (patch: Partial<CompactionSettings>) => {
    setCfg((p) => (p ? { ...p, ...patch } : p));
    await setCompactionSettings(patch, mid);
  };

  if (!cfg) return null;
  const modelLabel = (id: string) => labels[id]?.split(" · ")[0] || id;
  return (
    <div className={CARD + " p-4 mb-4"} data-testid="compaction-card">
      <div className={FIELD_LABEL}>{t("settingsx.compaction.title")}</div>
      <div className={FIELD_HELP}>
        {t("settingsx.compaction.help")}
      </div>

      <div className="mt-3 flex items-center gap-5 flex-wrap">
        <label className="flex items-center gap-2.5">
          <span className="text-ui text-ink">{t("settingsx.compaction.compact_at")}</span>
          <input
            type="number"
            min={10}
            max={95}
            value={Math.round(cfg.compaction_threshold_pct * 100)}
            data-testid="compaction-threshold"
            className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
            onChange={(e) =>
              save({
                compaction_threshold_pct:
                  Math.max(10, Math.min(Number(e.target.value) || 80, 95)) / 100,
              })
            }
          />
          <span className="text-ui text-muted">{t("settingsx.compaction.pct_of_window")}</span>
        </label>
        <label className="flex items-center gap-2.5">
          <span className="text-ui text-ink">{t("settingsx.compaction.or_at")}</span>
          <input
            type="number"
            min={10_000}
            max={2_000_000}
            step={10_000}
            value={cfg.compaction_cap_tokens}
            data-testid="compaction-cap"
            className="w-28 px-2 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
            onChange={(e) =>
              save({
                compaction_cap_tokens: Math.max(
                  10_000,
                  Math.min(Number(e.target.value) || 250_000, 2_000_000),
                ),
              })
            }
          />
          <span className="text-ui text-muted">{t("settingsx.compaction.tokens_smaller")}</span>
        </label>
      </div>
      <div className={FIELD_HELP}>
        {t("settingsx.compaction.cap_help")}
      </div>

      <div className="mt-3 flex items-center gap-2.5">
        <span className="text-ui text-ink">{t("settingsx.compaction.summarizer_model")}</span>
        <select
          value={cfg.compaction_model}
          data-testid="compaction-model"
          className="px-2 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
          onChange={(e) => save({ compaction_model: e.target.value })}
        >
          <option value="">{t("settingsx.compaction.own_model_default")}</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {modelLabel(m)}
            </option>
          ))}
        </select>
      </div>
      <div className={FIELD_HELP}>
        {t("settingsx.compaction.summarizer_help")}
      </div>
    </div>
  );
}

// -- Composer: context-window bar (owner ask 2026-07-30) ------------------------
// The chip's bar is context-window occupancy; the session total (unbounded) lives in
// the popover. Some people would rather not watch a meter at all, hence the toggle.
function ContextBarCard() {
  const { t } = useTranslation();
  const [shown, setShown] = useState<boolean | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => setShown(s.context_bar === true))
      .catch(() => setShown(false));
  }, []);

  const save = async (next: boolean) => {
    setShown(next);
    await setContextBar(next);
  };

  if (shown === null) return null;
  return (
    <div className={CARD + " p-4 mb-4"} data-testid="context-bar-card">
      <div className={FIELD_LABEL}>{t("settings.composer_section")}</div>
      <label className="flex items-start gap-3 py-2">
        <input
          type="checkbox"
          className="mt-0.5"
          data-testid="context-bar-toggle"
          checked={shown}
          onChange={(e) => save(e.target.checked)}
        />
        <span>
          <span className="block text-ui text-ink">{t("settings.context_bar_title")}</span>
          <span className="block text-meta text-muted">{t("settings.context_bar_desc")}</span>
        </span>
      </label>
    </div>
  );
}

// Auto-Approve (spec §1.5): the experimental feature flag that adds the "Auto-Approve" mode
// to the composer's mode picker, plus its shadow-evaluation sibling. Both default off and are
// user-global (a cloned repo can't turn either on). Shadow is nested under the main flag — it
// only makes sense to measure the reviewer once you know what it is.
function AutoApproveCard() {
  const { t } = useTranslation();
  const [on, setOn] = useState<boolean | null>(null);
  const [shadow, setShadow] = useState(false);

  useEffect(() => {
    getSettings()
      .then((s) => {
        setOn(s.auto_approve === true);
        setShadow(s.auto_approve_shadow === true);
      })
      .catch(() => setOn(false));
  }, []);

  const saveOn = async (next: boolean) => {
    setOn(next);
    await setAutoApprove(next);
  };
  const saveShadow = async (next: boolean) => {
    setShadow(next);
    await setAutoApproveShadow(next);
  };

  if (on === null) return null;
  return (
    <div className={CARD + " p-4 mb-4"} data-testid="auto-approve-card">
      <div className={FIELD_LABEL}>{t("settingsx.auto_approve.title")}</div>
      <label className="flex items-start gap-3 py-2">
        <input
          type="checkbox"
          className="mt-0.5"
          data-testid="auto-approve-toggle"
          checked={on}
          onChange={(e) => saveOn(e.target.checked)}
        />
        <span>
          <span className="block text-ui text-ink">{t("settingsx.auto_approve.enable")}</span>
          <span className="block text-meta text-muted">
            <Trans i18nKey="settingsx.auto_approve.enable_desc" components={{ em: <em /> }} />
          </span>
        </span>
      </label>
      <label className="flex items-start gap-3 py-2 pl-7">
        <input
          type="checkbox"
          className="mt-0.5"
          data-testid="auto-approve-shadow-toggle"
          checked={shadow}
          onChange={(e) => saveShadow(e.target.checked)}
        />
        <span>
          <span className="block text-ui text-ink">
            <Trans
              i18nKey="settingsx.auto_approve.shadow_title"
              components={{ faint: <span className="text-faint" /> }}
            />
          </span>
          <span className="block text-meta text-muted">
            <Trans i18nKey="settingsx.auto_approve.shadow_desc" components={{ em: <em /> }} />
          </span>
        </span>
      </label>
    </div>
  );
}

function SidebarCard() {
  const { t } = useTranslation();
  const [peek, setPeek] = useState<number | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => setPeek(s.sessions_peek || 5))
      .catch(() => setPeek(5));
  }, []);

  const save = async (n: number) => {
    const clamped = Math.max(1, Math.min(n || 5, 50));
    setPeek(clamped);
    await setSessionsPeek(clamped);
  };

  if (peek === null) return null;
  return (
    <div className={CARD + " p-4 mb-4"}>
      <div className={FIELD_LABEL}>{t("settings.sidebar_card_title")}</div>
      <label className="flex items-center gap-3 mt-2.5">
        <span className="text-ui text-ink">{t("settings.sidebar_per_coworker")}</span>
        <input
          type="number"
          min={1}
          max={50}
          value={peek}
          className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
          onChange={(e) => save(Number(e.target.value))}
        />
      </label>
      <div className={FIELD_HELP}>
        {t("settings.sidebar_card_help")}
      </div>
    </div>
  );
}

// -- Files (scratch location) — one card inside General (UX-021: a single option
// doesn't earn its own tab) -----------------------------------------------------
function FilesCard() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [scratchDraft, setScratchDraft] = useState("");
  const [scratchMsg, setScratchMsg] = useState<string | null>(null);
  const desktop = isTauri();

  const refresh = () =>
    getSettings()
      .then((s) => {
        setSettings(s);
        setScratchDraft((d) => d || s.scratch_base || "");
      })
      .catch(() => setSettings(null));
  useEffect(() => {
    refresh();
  }, []);

  const saveScratch = async () => {
    setScratchMsg(null);
    const res = await setScratchBase(scratchDraft.trim());
    if (res.ok) {
      setScratchMsg(t("settings.files_saved"));
      refresh();
    } else {
      setScratchMsg(res.error || t("settings.files_save_error"));
    }
  };
  const browseScratch = async () => {
    const picked = await pickFolder();
    if (picked) setScratchDraft(picked);
  };

  if (!settings) return null;

  return (
    <div className={CARD + " p-4 mb-4"}>
      <div className={FIELD_LABEL}>{t("settings.files_title")}</div>
        <div className="flex items-center gap-2 mt-2.5">
          <input
            className={INPUT}
            type="text"
            placeholder={t("settings.scratch_placeholder")}
            value={scratchDraft}
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => setScratchDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveScratch()}
          />
          {desktop && (
            <button className={BTN_BORDERED} onClick={browseScratch} title={t("settings.files_pick_folder")}>
              {t("settings.files_browse")}
            </button>
          )}
          <button className={BTN_ACCENT} onClick={saveScratch} disabled={!scratchDraft.trim()}>
            {t("settings.files_save")}
          </button>
        </div>
      <div className={FIELD_HELP}>
        {t("settings.files_help")}
      </div>
      {scratchMsg && <div className="text-ui text-muted mt-2.5">{scratchMsg}</div>}
    </div>
  );
}
