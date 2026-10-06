// Settings ▸ Sandbox (UX-051 A, UX-053 v5, OPE-207): one switch first; on reveals the
// sandbox type; a type that is the machine's choice reveals its options: the network
// (only the sites the user ticks, none until they do; or everything), the config
// and keys copied into sandboxes, and the tool folders agents may read. A type that is not
// set up yet looks disabled and carries one "Set up" button: the Windows sandbox's opens its
// one-time elevated setup, OpenShell's opens its setup: two checks the user fixes (Docker
// running, OpenShell installed) and one step the app does (the folder setting and the
// base image download, with progress).
// Everything here is machine-level (the machine's config.toml through /v1/settings/sandbox);
// nothing is per project. A provider change rebuilds live sessions under the new rule
// (onProviderChanged carries their ids).
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  cancelSandboxSetup,
  getSandboxReadiness,
  getSandboxSettings,
  getSandboxSetup,
  runSandboxRemove,
  runSandboxSetup,
  setSandboxSettings,
  startSandboxSetup,
  type Machine,
  type SandboxCredentialEntry,
  type SandboxReadiness,
  type SandboxReadinessStep,
  type SandboxSettings,
  type SandboxSetupRowState,
  type SandboxSetupState,
  type SandboxToolchainEntry,
} from "../api";
import { siDocker, siGithub, siGooglecloud, siKubernetes, siNpm, siTerraform } from "simple-icons";
import { chooseFolder, openExternal } from "../tauri";
import { Toggle } from "./Toggle";
import { PanelHead } from "./IntegrationsView";

type T = (k: string, o?: Record<string, unknown>) => string;

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-ui font-medium text-ink";
const INPUT =
  "flex-1 min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent";
const BTN_ACCENT = "text-ui px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED = "text-ui px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0 disabled:opacity-40";
const BTN_SMALL = "text-meta px-2.5 py-1 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";
const TAG = "inline-flex items-center rounded px-1.5 text-label font-medium leading-5";
const FOOT = "flex items-center gap-3.5 pl-6 pr-4 py-3 bg-chrome border-t border-line rounded-b-xl2";

type SetupStage = "ask" | "working" | "done" | "failed";

function Chevron({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" className={"w-5 h-5 text-faint transition-transform shrink-0 " + (open ? "rotate-90" : "")} aria-hidden="true">
      <path d="M8 5l5 5-5 5" />
    </svg>
  );
}

const KEY_ICON = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="8" cy="15" r="4" />
    <path d="M11 12l9-9M17 6l3 3M14 9l2 2" />
  </svg>
);
const TOOL_ICON = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.4 2.4-2-2z" />
  </svg>
);

// A panel that is one line until opened: chevron, icon, title and a one-line summary.
function Panel({
  id,
  open,
  onToggle,
  icon,
  title,
  summary,
  children,
}: {
  id: string;
  open: boolean;
  onToggle: () => void;
  icon: React.ReactNode;
  title: string;
  summary: string;
  children: React.ReactNode;
}) {
  return (
    <div className={CARD + " mb-2.5 relative"} data-testid={`sandbox-card-${id}`}>
      <button
        type="button"
        className={"w-full text-left flex items-center gap-3 px-4 py-3 hover:bg-chrome rounded-xl2 " + (open ? "border-b border-line rounded-b-none" : "")}
        onClick={onToggle}
        aria-expanded={open}
        data-testid={`sandbox-card-${id}-toggle`}
      >
        <Chevron open={open} />
        <span className={"w-[34px] h-[34px] rounded-[9px] flex items-center justify-center shrink-0 " + (open ? "bg-accentSoft text-accent" : "bg-paper text-muted")}>{icon}</span>
        <span className="flex-1 min-w-0">
          <span className="block text-ui font-medium text-ink">{title}</span>
          <span className="block text-meta text-muted" data-testid={`sandbox-card-${id}-summary`}>
            {summary}
          </span>
        </span>
      </button>
      {open ? <div data-testid={`sandbox-card-${id}-body`}>{children}</div> : null}
    </div>
  );
}

function Modal({ children, testid, wide }: { children: React.ReactNode; testid: string; wide?: boolean }) {
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" data-testid={testid}>
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[1px]" />
      <div className={"absolute left-1/2 top-[10vh] -translate-x-1/2 max-w-[94vw] max-h-[80vh] rounded-xl2 border border-line bg-panel shadow-2xl overflow-auto p-6 " + (wide ? "w-[680px]" : "w-[640px]")}>
        {children}
      </div>
    </div>
  );
}

// The small square before an entry: the tool's own mark in its brand colour (simple-icons,
// CC0), a key for SSH keys, else a folder.
// AWS's mark was removed from simple-icons after v9; its path is vendored from v9 (CC0), as
// connectors/registry.tsx does for Slack.
const AWS_PATH =
  "M6.763 10.036c0 .296.032.535.088.71.064.176.144.368.256.576.04.063.056.127.056.183 0 .08-.048.16-.152.24l-.503.335a.383.383 0 0 1-.208.072c-.08 0-.16-.04-.239-.112a2.47 2.47 0 0 1-.287-.375 6.18 6.18 0 0 1-.248-.471c-.622.734-1.405 1.101-2.347 1.101-.67 0-1.205-.191-1.596-.574-.391-.384-.59-.894-.59-1.533 0-.678.239-1.23.726-1.644.487-.415 1.133-.623 1.955-.623.272 0 .551.024.846.064.296.04.6.104.918.176v-.583c0-.607-.127-1.03-.375-1.277-.255-.248-.686-.367-1.3-.367-.28 0-.568.031-.863.103-.295.072-.583.16-.862.272a2.287 2.287 0 0 1-.28.104.488.488 0 0 1-.127.023c-.112 0-.168-.08-.168-.247v-.391c0-.128.016-.224.056-.28a.597.597 0 0 1 .224-.167c.279-.144.614-.264 1.005-.36a4.84 4.84 0 0 1 1.246-.151c.95 0 1.644.216 2.091.647.439.43.662 1.085.662 1.963v2.586zm-3.24 1.214c.263 0 .534-.048.822-.144.287-.096.543-.271.758-.51.128-.152.224-.32.272-.512.047-.191.08-.423.08-.694v-.335a6.66 6.66 0 0 0-.735-.136 6.02 6.02 0 0 0-.75-.048c-.535 0-.926.104-1.19.32-.263.215-.39.518-.39.917 0 .375.095.655.295.846.191.2.47.296.838.296zm6.41.862c-.144 0-.24-.024-.304-.08-.064-.048-.12-.16-.168-.311L7.586 5.55a1.398 1.398 0 0 1-.072-.32c0-.128.064-.2.191-.2h.783c.151 0 .255.025.31.08.065.048.113.16.16.312l1.342 5.284 1.245-5.284c.04-.16.088-.264.151-.312a.549.549 0 0 1 .32-.08h.638c.152 0 .256.025.32.08.063.048.12.16.151.312l1.261 5.348 1.381-5.348c.048-.16.104-.264.16-.312a.52.52 0 0 1 .311-.08h.743c.127 0 .2.065.2.2 0 .04-.009.08-.017.128a1.137 1.137 0 0 1-.056.2l-1.923 6.17c-.048.16-.104.263-.168.311a.51.51 0 0 1-.303.08h-.687c-.151 0-.255-.024-.32-.08-.063-.056-.119-.16-.15-.32l-1.238-5.148-1.23 5.14c-.04.16-.087.264-.15.32-.065.056-.177.08-.32.08zm10.256.215c-.415 0-.83-.048-1.229-.143-.399-.096-.71-.2-.918-.32-.128-.071-.215-.151-.247-.223a.563.563 0 0 1-.048-.224v-.407c0-.167.064-.247.183-.247.048 0 .096.008.144.024.048.016.12.048.2.08.271.12.566.215.878.279.319.064.63.096.95.096.502 0 .894-.088 1.165-.264a.86.86 0 0 0 .415-.758.777.777 0 0 0-.215-.559c-.144-.151-.416-.287-.807-.415l-1.157-.36c-.583-.183-1.014-.454-1.277-.813a1.902 1.902 0 0 1-.4-1.158c0-.335.073-.63.216-.886.144-.255.335-.479.575-.654.24-.184.51-.32.83-.415.32-.096.655-.136 1.006-.136.175 0 .359.008.535.032.183.024.35.056.518.088.16.04.312.08.455.127.144.048.256.096.336.144a.69.69 0 0 1 .24.2.43.43 0 0 1 .071.263v.375c0 .168-.064.256-.184.256a.83.83 0 0 1-.303-.096 3.652 3.652 0 0 0-1.532-.311c-.455 0-.815.071-1.062.223-.248.152-.375.383-.375.71 0 .224.08.416.24.567.159.152.454.304.877.44l1.134.358c.574.184.99.44 1.237.767.247.327.367.702.367 1.117 0 .343-.072.655-.207.926-.144.272-.336.511-.583.703-.248.2-.543.343-.886.447-.36.111-.734.167-1.142.167zM21.698 16.207c-2.626 1.94-6.442 2.969-9.722 2.969-4.598 0-8.74-1.7-11.87-4.526-.247-.223-.024-.527.272-.351 3.384 1.963 7.559 3.153 11.877 3.153 2.914 0 6.114-.607 9.06-1.852.439-.2.814.287.383.607zM22.792 14.961c-.336-.43-2.22-.207-3.074-.103-.255.032-.295-.192-.063-.36 1.5-1.053 3.967-.75 4.254-.399.287.36-.08 2.826-1.485 4.007-.215.184-.423.088-.327-.151.32-.79 1.03-2.57.695-2.994z";
const BADGES: Record<string, [string, string]> = {
  gh: [siGithub.path, `#${siGithub.hex}`],
  aws: [AWS_PATH, "#FF9900"],
  "aws-credentials": [AWS_PATH, "#FF9900"],
  kube: [siKubernetes.path, `#${siKubernetes.hex}`],
  npm: [siNpm.path, `#${siNpm.hex}`],
  docker: [siDocker.path, `#${siDocker.hex}`],
  gcloud: [siGooglecloud.path, `#${siGooglecloud.hex}`],
  terraform: [siTerraform.path, `#${siTerraform.hex}`],
};
function Badge({ name }: { name: string }) {
  const known = BADGES[name];
  return (
    <span className="w-[26px] h-[26px] rounded-[7px] inline-flex items-center justify-center shrink-0 bg-panel text-muted border border-line" data-testid={`sandbox-badge-${name}`}>
      {known ? (
        <svg viewBox="0 0 24 24" width="15" height="15" fill={known[1]} aria-hidden="true">
          <path d={known[0]} />
        </svg>
      ) : name === "ssh" ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="8" cy="15" r="4" />
          <path d="M11 12l9-9M17 6l3 3M14 9l2 2" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        </svg>
      )}
    </span>
  );
}

const SETUP_CHANGES = ["accounts", "rules", "folder", "record"] as const;

// "a, b and c", or "a, b, c and 5 more" past `max`.
function listText(t: T, names: string[], max: number): string {
  if (names.length <= 1) return names.join("");
  if (names.length > max) return t("settingsx.sandbox.list_more", { list: names.slice(0, max).join(", "), count: names.length - max });
  return t("settingsx.sandbox.list_and", { list: names.slice(0, -1).join(", "), last: names[names.length - 1] });
}

export function SandboxSection({ machine, onProviderChanged }: { machine?: Machine | null; onProviderChanged?: (sessionIds: string[]) => void }) {
  const { t } = useTranslation();
  const mid = machine?.id ?? null;
  const [cfg, setCfg] = useState<SandboxSettings | null>(null);
  const [error, setError] = useState<string>("");
  const [readiness, setReadiness] = useState<SandboxReadiness | null>(null);
  const [job, setJob] = useState<SandboxSetupState | null>(null);
  const [copied, setCopied] = useState<string>("");
  const notifiedRef = useRef(false);
  const [wantOn, setWantOn] = useState(false); // the switch is on, no type is the choice yet
  const [openFiles, setOpenFiles] = useState(false);
  const [openTools, setOpenTools] = useState(false);
  const [menu, setMenu] = useState(false); // the "Add…" menu of the files panel
  const [picking, setPicking] = useState(false); // the "A CLI's login" picker
  const [editing, setEditing] = useState<string | null>(null); // credential name being edited, "" = new
  const [sitesOpen, setSitesOpen] = useState(false);
  const [addingTool, setAddingTool] = useState(false);
  const [toolTitle, setToolTitle] = useState("");
  const [toolPath, setToolPath] = useState("~/");
  const [setupStage, setSetupStage] = useState<SetupStage | null>(null);
  const [setupOutcome, setSetupOutcome] = useState<{ checked?: string; error?: string }>({});
  const [openshellDialog, setOpenshellDialog] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removeBusy, setRemoveBusy] = useState(false);

  useEffect(() => {
    getSandboxSettings(mid).then(setCfg).catch(() => setCfg(null));
  }, [mid]);

  const save = async (patch: Parameters<typeof setSandboxSettings>[0]) => {
    const res = await setSandboxSettings(patch, mid);
    if (!res.ok) {
      setError(res.error || "could not save");
      return false;
    }
    setError("");
    setCfg(res as SandboxSettings);
    if ("provider" in patch) onProviderChanged?.(res.rebuilt_sessions ?? []);
    return true;
  };

  // OpenShell's checklist, loaded when its setup dialog opens and again after a run.
  const loadReadiness = () => {
    setReadiness(null);
    getSandboxReadiness(mid)
      .then(setReadiness)
      .catch(() => setReadiness({ platform: cfg?.platform ?? "", supported: false, steps: [], all_ok: false }));
  };
  useEffect(() => {
    if (openshellDialog) loadReadiness();
  }, [openshellDialog, mid]); // eslint-disable-line react-hooks/exhaustive-deps
  // A setup job may be running from before (the page was closed and reopened): adopt it.
  useEffect(() => {
    if (!cfg || cfg.platform === "win32") return;
    getSandboxSetup(mid)
      .then((s) => setJob(s.status === "idle" ? null : s))
      .catch(() => {});
  }, [mid, cfg?.platform]); // eslint-disable-line react-hooks/exhaustive-deps
  // Poll the job while it runs; on the way out, reload the checklist and the settings
  // (the job writes the config line last) and say so once.
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const timer = window.setInterval(() => {
      getSandboxSetup(mid)
        .then((s) => {
          setJob(s);
          if (s.status !== "running") {
            loadReadiness();
            getSandboxSettings(mid).then(setCfg).catch(() => {});
            if (s.status === "done" && !notifiedRef.current) {
              notifiedRef.current = true;
              try {
                if ("Notification" in window && Notification.permission === "granted") {
                  new Notification(t("settingsx.sandbox.notify_title"), { body: t("settingsx.sandbox.notify_body") });
                }
              } catch {
                /* notifications are a courtesy */
              }
            }
          }
        })
        .catch(() => {});
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.status, mid]); // eslint-disable-line react-hooks/exhaustive-deps
  const startJob = async () => {
    notifiedRef.current = false;
    try {
      if ("Notification" in window && Notification.permission === "default") Notification.requestPermission().catch(() => {});
    } catch {
      /* ignore */
    }
    const s = await startSandboxSetup(mid).catch(() => null);
    if (s) setJob(s);
  };
  const cancelJob = async () => {
    const s = await cancelSandboxSetup(mid).catch(() => null);
    if (s) setJob(s);
  };
  const copy = (text: string) => {
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(text);
        window.setTimeout(() => setCopied(""), 1500);
      })
      .catch(() => {});
  };

  if (!cfg) return null;
  const isWindows = cfg.platform === "win32";
  const isMac = cfg.platform === "darwin";
  const providerNames: Record<string, [string, string]> = {
    seatbelt: [t("settingsx.sandbox.provider_seatbelt"), t("settingsx.sandbox.provider_seatbelt_desc")],
    windows: [t("settingsx.sandbox.provider_windows"), t("settingsx.sandbox.provider_windows_desc")],
    openshell: [t("settingsx.sandbox.provider_openshell"), isMac ? t("settingsx.sandbox.provider_openshell_desc_mac") : t("settingsx.sandbox.provider_openshell_desc")],
  };
  const chosen = cfg.provider || cfg.effective_provider || "direct";
  const active = chosen !== "direct"; // a type is the machine's choice
  const on = active || wantOn;
  const setup = cfg.windows_setup;
  const setupReady = setup?.state === "ready";
  const types = cfg.providers.filter((p) => p.name !== "direct" && (p.name !== "seatbelt" || isMac) && (p.name !== "windows" || isWindows));
  const shipped = (name: string, key: "title" | "does", fallback?: string) => {
    const k = `settingsx.sandbox.${key}_${name}`;
    const v = t(k);
    return v === k ? fallback || "" : v;
  };
  const titleOf = (c: SandboxCredentialEntry) => c.title || shipped(c.name, "title", c.name);
  const doesOf = (c: SandboxCredentialEntry) => c.does || shipped(c.name, "does");
  const updateCredentials = (rows: SandboxCredentialEntry[]) => save({ credentials: rows.map(({ kind: _k, shipped: _s, ...row }) => row) });
  const updateToolchains = (rows: SandboxToolchainEntry[]) => save({ toolchains: rows.map(({ exists: _e, shipped: _s, ...row }) => row) });

  const flip = (next: boolean) => {
    if (next) {
      setWantOn(true);
      return;
    }
    setWantOn(false);
    if (active) void save({ provider: "direct" });
  };

  const openWindowsSetup = () => {
    setSetupOutcome({});
    setSetupStage("ask");
  };

  const runSetup = async () => {
    setSetupStage("working");
    const res = await runSandboxSetup(mid);
    if (res.ok) {
      setCfg(res as SandboxSettings);
      setWantOn(false);
      setSetupOutcome({ checked: res.checked });
      setSetupStage("done");
    } else {
      if (res.platform) setCfg(res as SandboxSettings);
      setSetupOutcome({ error: res.error || "setup did not finish" });
      setSetupStage("failed");
    }
  };

  const runRemove = async () => {
    setRemoveBusy(true);
    const res = await runSandboxRemove(mid);
    setRemoveBusy(false);
    if (res.ok) {
      setCfg(res as SandboxSettings);
      setWantOn(false);
      setRemoving(false);
    } else {
      setError(res.error || "could not remove the setup");
      setRemoving(false);
    }
  };

  const setUpOn = setup?.set_up_at ? new Date(setup.set_up_at) : null;
  const setUpOnText = setUpOn && !Number.isNaN(setUpOn.getTime()) ? setUpOn.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";

  const ticked = cfg.network_hosts ?? [];
  const bare = (h: string) => h.replace(/:443$/, "");
  const presets = cfg.credential_presets ?? [];
  const showTools = cfg.platform !== "linux" && chosen !== "openshell" && Boolean(cfg.toolchains);
  const toolsOn = (cfg.toolchains || []).filter((x) => x.enabled);
  const filesOn = cfg.credentials.filter((c) => c.enabled);
  const filesSummary = filesOn.length ? t("settingsx.sandbox.files_sum", { list: listText(t, filesOn.map(titleOf), 3) }) : t("settingsx.sandbox.files_sum_none");
  const toolsSummary = toolsOn.length ? t("settingsx.sandbox.tools_sum", { list: listText(t, toolsOn.map((x) => x.title || x.name), 3) }) : t("settingsx.sandbox.tools_sum_none");
  const keyNote = isWindows ? t("settingsx.sandbox.credman_note") : isMac ? t("settingsx.sandbox.keychain_note") : t("settingsx.sandbox.files_foot");
  const addMenu = (
    <span className="relative">
      <button className={BTN_SMALL} onClick={() => setMenu(!menu)} aria-expanded={menu} data-testid="sandbox-credential-add">
        {t("settingsx.sandbox.add_menu")}
      </button>
      {menu ? (
        <>
          <span className="fixed inset-0 z-10" onClick={() => setMenu(false)} />
          <span className="absolute right-0 bottom-9 z-20 w-[300px] rounded-xl border border-line bg-panel shadow-xl p-1.5 text-left" role="menu" data-testid="sandbox-add-menu">
            {(
              [
                ["cli", () => setPicking(true)],
                ["file", () => setEditing("")],
              ] as const
            ).map(([k, go]) => (
              <button
                key={k}
                role="menuitem"
                className="block w-full text-left px-3 py-2 rounded-lg hover:bg-chrome"
                onClick={() => {
                  setMenu(false);
                  go();
                }}
                data-testid={`sandbox-add-${k}`}
              >
                <span className="block text-ui font-medium text-ink">{t(`settingsx.sandbox.add_${k}`)}</span>
                <span className="block text-meta text-muted">{t(`settingsx.sandbox.add_${k}_desc`)}</span>
              </button>
            ))}
          </span>
        </>
      ) : null}
    </span>
  );

  return (
    <section data-testid="sandbox-section">
      <PanelHead title={t("settingsx.sandbox.title")} sub={machine ? t("settingsx.sandbox.sub_machine", { name: machine.name }) : t("settingsx.sandbox.sub")} />
      {error ? <div className="mb-3 text-meta text-danger">{error}</div> : null}

      {/* 1. The switch */}
      <div className={CARD + " mb-5"}>
        <div className="flex items-start gap-3.5 px-4 py-3.5">
          <span className="mt-0.5">
            <Toggle checked={on} onChange={flip} title={t("settingsx.sandbox.switch_title")} />
          </span>
          <span className="flex-1 min-w-0">
            <span className="block text-ui font-medium text-ink">{t("settingsx.sandbox.switch_title")}</span>
            <span className="block text-meta text-muted max-w-[640px]">{t("settingsx.sandbox.switch_desc")}</span>
            {/* OPE-209: the switch, the type and the network list apply when a session starts;
                an open session keeps the walls it started with. Said here, once. */}
            <span className="block text-meta text-faint max-w-[640px] mt-0.5" data-testid="sandbox-applies-note">{t("settingsx.sandbox.applies_note")}</span>
          </span>
        </div>
      </div>
      {cfg.refused ? <div className="-mt-3 mb-4 text-meta text-danger">{t("settingsx.sandbox.refused", { why: cfg.refused })}</div> : null}

      {/* 2. The type, once the switch is on */}
      {on ? (
        <>
          <div className={FIELD_LABEL + " mb-2"}>{t("settingsx.sandbox.type")}</div>
          <div className={CARD + " mb-5 divide-y divide-line"} role="radiogroup" aria-label={t("settingsx.sandbox.type")}>
            {types.map((p) => {
              const [label, desc] = providerNames[p.name] ?? [p.name, ""];
              const isActive = chosen === p.name;
              const win = p.name === "windows";
              const os = p.name === "openshell";
              // Ready to choose: usable, and for Windows set up. Otherwise the row looks
              // disabled and its one button sets it up, where this machine can.
              const ready = win ? setupReady && p.usable : p.usable;
              const canSetUp = win ? !setupReady && Boolean(setup?.can_elevate) : os && !isWindows && !p.usable;
              const why = ready || canSetUp ? "" : win && setup && !setup.can_elevate ? t("settingsx.sandbox.needs_admin_why", { command: setup.command }) : p.why;
              const osRunning = os && job?.status === "running";
              return (
                <div key={p.name} className="flex items-start gap-3 px-4 py-3" data-testid={`sandbox-type-${p.name}`}>
                  <label className={"flex items-start gap-3 flex-1 min-w-0 " + (ready ? "cursor-pointer" : "")}>
                    <input
                      type="radio"
                      name="sandbox-provider"
                      className={"mt-1 " + (ready || isActive ? "" : "opacity-50")}
                      checked={isActive}
                      disabled={!ready && !isActive}
                      onChange={() => void save({ provider: p.name })}
                      data-testid={`sandbox-provider-${p.name}`}
                    />
                    <span className="flex-1 min-w-0">
                      <span className={"block text-ui " + (ready || isActive ? "text-ink" : "text-ink/50")}>{label}</span>
                      <span className={"block text-meta " + (ready || isActive ? "text-muted" : "text-muted/60")}>{desc}</span>
                      {why ? (
                        <span className="block text-meta text-warnInk mt-1" data-testid={`sandbox-provider-${p.name}-why`}>
                          {why}
                        </span>
                      ) : null}
                      {isActive && os && p.state === "needs_download" ? (
                        <span className="block text-meta text-warnInk mt-1" data-testid={`sandbox-provider-${p.name}-hint`}>
                          {t("settingsx.sandbox.needs_download_hint")}
                        </span>
                      ) : null}
                      {win && setupReady ? (
                        <span className="block text-meta text-muted mt-1.5" data-testid="sandbox-windows-setup-line">
                          {setUpOnText ? t("settingsx.sandbox.set_up_on", { date: setUpOnText }) : t("settingsx.sandbox.set_up_done")}
                          <span className="mx-1.5 text-faint">·</span>
                          <button type="button" className="text-accent hover:underline" onClick={() => setRemoving(true)} data-testid="sandbox-remove-setup">
                            {t("settingsx.sandbox.remove_setup")}
                          </button>
                        </span>
                      ) : null}
                    </span>
                  </label>
                  {canSetUp ? (
                    <button
                      className={BTN_SMALL + " self-center"}
                      onClick={() => (win ? openWindowsSetup() : setOpenshellDialog(true))}
                      data-testid={`sandbox-setup-${p.name}`}
                    >
                      {osRunning ? t("settingsx.sandbox.setup_running") : t("settingsx.sandbox.set_up")}
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        </>
      ) : null}

      {/* 3. The type's own options */}
      {active ? (
        <>
          <div className={FIELD_LABEL + " mb-2"}>{t("settingsx.sandbox.network")}</div>
          <div className={CARD + " mb-5 divide-y divide-line"} role="radiogroup" aria-label={t("settingsx.sandbox.network")}>
            {/* The server lists the choices: with OpenShell there is no "Allow everything". */}
            {(["allowlist", "open"] as const).filter((name) => cfg.network_profiles.some((p) => p.name === name)).map((name) => (
              <label key={name} className="flex items-start gap-3 px-4 py-2.5 cursor-pointer">
                <input
                  type="radio"
                  name="sandbox-network"
                  className="mt-1"
                  checked={cfg.network_profile === name}
                  onChange={() => save({ network_profile: name })}
                  data-testid={`sandbox-network-${name}`}
                />
                <span className="flex-1 min-w-0">
                  <span className={"block text-ui " + (name === "open" ? "text-warnInk" : "text-ink")}>{t(`settingsx.sandbox.profile_${name}`)}</span>
                  <span className="block text-meta text-muted" data-testid={`sandbox-network-${name}-desc`}>
                    {name === "open"
                      ? t("settingsx.sandbox.profile_open_desc")
                      : ticked.length
                        ? t("settingsx.sandbox.sites_sum", { list: listText(t, ticked.map(bare), 3) })
                        : t("settingsx.sandbox.sites_none")}
                    {name === "allowlist" ? (
                      <button
                        type="button"
                        className="ml-1.5 text-accent hover:underline"
                        onClick={(e) => {
                          e.preventDefault();
                          setSitesOpen(true);
                        }}
                        data-testid="sandbox-network-sites"
                      >
                        {t("settingsx.sandbox.choose_sites")}
                      </button>
                    ) : null}
                  </span>
                </span>
              </label>
            ))}
          </div>

          <Panel id="files" open={openFiles} onToggle={() => setOpenFiles(!openFiles)} icon={KEY_ICON} title={t("settingsx.sandbox.files_panel")} summary={filesSummary}>
            {cfg.credentials.length ? (
              <div className="divide-y divide-line">
                {cfg.credentials.map((c) => (
                  <div key={c.name} className="group flex items-start gap-3 pl-6 pr-4 py-3" data-testid={`sandbox-credential-${c.name}`}>
                    <span className="mt-0.5">
                      <Toggle checked={c.enabled} onChange={(next) => updateCredentials(cfg.credentials.map((x) => (x.name === c.name ? { ...x, enabled: next } : x)))} title={titleOf(c)} />
                    </span>
                    <Badge name={c.name} />
                    <span className="flex-1 min-w-0">
                      <span className="flex items-center flex-wrap gap-2 text-ui text-ink">
                        {titleOf(c)}
                        <code className="text-meta text-muted font-mono">{c.path}</code>
                        {c.kind ? (
                          <span className={TAG + " bg-accentSoft text-accent"} data-testid={`sandbox-credential-${c.name}-kind`}>
                            {t(c.kind === "folder" ? "settingsx.sandbox.kind_folder" : "settingsx.sandbox.kind_file")}
                          </span>
                        ) : (
                          <span className="text-meta text-faint">{t("settingsx.sandbox.credential_missing")}</span>
                        )}
                        <span className={TAG + " " + (c.label === "configuration" ? "bg-paper text-muted" : "bg-warnSoft text-warnInk")}>
                          {t(c.label === "configuration" ? "settingsx.sandbox.label_configuration" : "settingsx.sandbox.label_credential")}
                        </span>
                      </span>
                      <span className="block text-meta text-muted">
                        {doesOf(c)}{" "}
                        {c.hosts && c.hosts.length && cfg.network_profile !== "open" ? <span className="text-faint">{t("settingsx.sandbox.also_allows", { hosts: c.hosts.join(", ") })}</span> : null}
                      </span>
                    </span>
                    <span className="text-meta text-muted shrink-0 whitespace-nowrap opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                      <button className="hover:text-ink" onClick={() => setEditing(c.name)} data-testid={`sandbox-credential-${c.name}-edit`}>
                        {t("settingsx.sandbox.edit")}
                      </button>
                      <span className="mx-1.5 text-faint">·</span>
                      <button className="hover:text-ink" onClick={() => updateCredentials(cfg.credentials.filter((x) => x.name !== c.name))} data-testid={`sandbox-credential-${c.name}-remove`}>
                        {t("settingsx.sandbox.remove")}
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="px-6 py-4 text-ui text-muted" data-testid="sandbox-files-empty">
                {t("settingsx.sandbox.files_empty")}
              </div>
            )}
            <div className={FOOT}>
              <span className="text-meta text-faint flex-1">{cfg.credentials.length ? keyNote : t("settingsx.sandbox.files_foot")}</span>
              {addMenu}
            </div>
          </Panel>

          {showTools ? (
            <Panel id="tools" open={openTools} onToggle={() => setOpenTools(!openTools)} icon={TOOL_ICON} title={t("settingsx.sandbox.tools_panel")} summary={toolsSummary}>
              <div className="divide-y divide-line">
                {cfg.toolchains.map((tc) => (
                  <div key={tc.name} className="flex items-center gap-3 pl-6 pr-4 py-2" data-testid={`sandbox-toolchain-${tc.name}`}>
                    <Toggle checked={tc.enabled} onChange={(next) => updateToolchains(cfg.toolchains.map((x) => (x.name === tc.name ? { ...x, enabled: next } : x)))} title={tc.title || tc.name} />
                    <span className="flex-1 min-w-0 text-ui text-ink">
                      {tc.title || tc.name}
                      {tc.exists === false ? <span className="text-meta text-faint"> · {t("settingsx.sandbox.toolchain_missing")}</span> : null}
                      {!tc.shipped ? <span className="text-meta text-faint"> · {t("settingsx.sandbox.added_by_you")}</span> : null}
                    </span>
                    <code className="text-meta text-muted font-mono shrink-0">{tc.path}</code>
                    {!tc.shipped ? (
                      <button className="text-meta text-muted hover:text-ink shrink-0" onClick={() => updateToolchains(cfg.toolchains.filter((x) => x.name !== tc.name))}>
                        {t("settingsx.sandbox.remove")}
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
              {addingTool ? (
                <div className="pl-6 pr-4 py-3 border-t border-line" data-testid="sandbox-toolchain-editor">
                  <div className="grid grid-cols-[150px_1fr] gap-x-3 gap-y-2 items-center">
                    <label className="text-ui text-muted">{t("settingsx.sandbox.field_title")}</label>
                    <input className={INPUT} value={toolTitle} onChange={(e) => setToolTitle(e.target.value)} />
                    <label className="text-ui text-muted">{t("settingsx.sandbox.field_toolchain_path")}</label>
                    <div className="flex gap-2">
                      <input className={INPUT + " font-mono"} value={toolPath} onChange={(e) => setToolPath(e.target.value)} />
                      <button
                        className={BTN_BORDERED}
                        onClick={async () => {
                          const picked = await chooseFolder();
                          if (picked) setToolPath(picked);
                        }}
                      >
                        {t("settingsx.sandbox.browse")}
                      </button>
                    </div>
                  </div>
                  <div className="flex justify-end gap-2 mt-3">
                    <button className={BTN_BORDERED} onClick={() => setAddingTool(false)}>
                      {t("settingsx.sandbox.cancel")}
                    </button>
                    <button
                      className={BTN_ACCENT}
                      disabled={!validPath(toolPath)}
                      onClick={() => {
                        const slug = (toolTitle || toolPath).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
                        setAddingTool(false);
                        setToolTitle("");
                        setToolPath("~/");
                        updateToolchains([...cfg.toolchains, { name: slug, title: toolTitle || undefined, path: toolPath, enabled: true }]);
                      }}
                    >
                      {t("settingsx.sandbox.add_button")}
                    </button>
                  </div>
                </div>
              ) : null}
              <div className={FOOT}>
                <span className="text-meta text-faint flex-1">{t("settingsx.sandbox.tools_foot")}</span>
                <button className={BTN_SMALL} onClick={() => setAddingTool(true)} data-testid="sandbox-toolchain-add">
                  {t("settingsx.sandbox.add_toolchain")}
                </button>
              </div>
            </Panel>
          ) : null}
          <div className="text-meta text-faint mt-5">{t("settingsx.sandbox.saved_in", { path: cfg.config_path })}</div>
        </>
      ) : null}

      {/* Choose sites: the allow list, every site a tick box */}
      {sitesOpen ? (
        <SitesDialog
          t={t}
          ticked={ticked}
          catalogue={cfg.network_sites ?? []}
          onCancel={() => setSitesOpen(false)}
          onSave={async (next) => {
            if (await save({ network_hosts: next })) setSitesOpen(false);
          }}
          error={error}
        />
      ) : null}

      {/* Add a CLI's login */}
      {picking ? (
        <Modal testid="sandbox-cli-picker" wide>
          <h3 className="text-heading font-semibold mb-1.5">{t("settingsx.sandbox.cli_title")}</h3>
          <p className="text-ui text-muted mb-3.5 leading-relaxed">{t("settingsx.sandbox.cli_intro")}</p>
          <div className="rounded-lg border border-line divide-y divide-line">
            {presets.length ? (
              [...presets]
                .sort((a, b) => Number(!a.kind) - Number(!b.kind))
                .map((p) => (
                  <div key={p.name} className="flex items-center gap-3 px-3 py-2.5" data-testid={`sandbox-preset-${p.name}`} data-found={p.kind ? "yes" : "no"}>
                    <Badge name={p.name} />
                    <span className="flex-1 min-w-0">
                      <span className={"block text-ui font-medium " + (p.kind ? "text-ink" : "text-faint")}>{titleOf(p)}</span>
                      <span className={"block text-meta " + (p.kind ? "text-muted" : "text-faint")}>
                        {p.kind ? `${p.path} · ${doesOf(p).replace(/\.$/, "")}` : isMac ? t("settingsx.sandbox.not_on_mac") : isWindows ? t("settingsx.sandbox.not_on_pc") : t("settingsx.sandbox.credential_missing")}
                      </span>
                    </span>
                    {p.kind ? (
                      <button
                        className={BTN_SMALL}
                        onClick={() => updateCredentials([...cfg.credentials, { name: p.name, enabled: true }])}
                        data-testid={`sandbox-preset-${p.name}-add`}
                      >
                        {t("settingsx.sandbox.add_button")}
                      </button>
                    ) : (
                      <span className="text-meta text-faint">{t("settingsx.sandbox.not_found")}</span>
                    )}
                  </div>
                ))
            ) : (
              <div className="px-3 py-3 text-ui text-muted">{t("settingsx.sandbox.cli_all_added")}</div>
            )}
          </div>
          <div className="flex items-center gap-2 mt-4">
            <span className="text-meta text-faint">{t("settingsx.sandbox.cli_not_listed")}</span>
            <span className="flex-1" />
            <button
              className={BTN_BORDERED}
              onClick={() => {
                setPicking(false);
                setEditing("");
              }}
            >
              {t("settingsx.sandbox.cli_file_button")}
            </button>
            <button
              className={BTN_ACCENT}
              onClick={() => {
                setPicking(false);
                setOpenFiles(true);
              }}
              data-testid="sandbox-cli-done"
            >
              {t("settingsx.sandbox.done")}
            </button>
          </div>
        </Modal>
      ) : null}

      {/* Add or edit a file or folder */}
      {editing !== null ? (
        <CredentialEditor
          entry={editing ? cfg.credentials.find((c) => c.name === editing) ?? null : null}
          titleOf={titleOf}
          doesOf={doesOf}
          onCancel={() => setEditing(null)}
          onSave={(row) => {
            const rows = editing ? cfg.credentials.map((x) => (x.name === editing ? row : x)) : [...cfg.credentials, row];
            setEditing(null);
            setOpenFiles(true);
            updateCredentials(rows);
          }}
        />
      ) : null}

      {/* OpenShell's setup dialog */}
      {openshellDialog ? (
        <OpenShellDialog
          t={t}
          platform={cfg.platform}
          readiness={readiness}
          job={job}
          ready={Boolean(cfg.providers.find((p) => p.name === "openshell")?.usable) && chosen === "openshell"}
          onStart={startJob}
          onCancel={cancelJob}
          onCheck={() => {
            setJob(null);
            loadReadiness();
          }}
          onClose={() => setOpenshellDialog(false)}
          copy={copy}
          copied={copied}
        />
      ) : null}

      {/* The Windows setup dialog */}
      {setupStage ? (
        <Modal testid="sandbox-setup-dialog">
          {setupStage === "ask" ? (
            <>
              <h3 className="text-heading font-semibold mb-1.5">{t("settingsx.sandbox.setup_title")}</h3>
              <p className="text-ui text-muted mb-3.5 leading-relaxed">{t("settingsx.sandbox.setup_intro")}</p>
              <div className="text-label font-medium text-faint mb-1.5">{t("settingsx.sandbox.setup_changes")}</div>
              <div className="grid gap-2.5">
                {SETUP_CHANGES.map((k) => (
                  <div key={k} className="text-ui leading-relaxed">
                    {t(`settingsx.sandbox.setup_change_${k}`)}
                    <div className="text-meta text-muted">{t(`settingsx.sandbox.setup_change_${k}_desc`)}</div>
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-2 mt-4">
                <span className="text-meta text-faint max-w-[320px] leading-relaxed">{t("settingsx.sandbox.setup_not_now_note")}</span>
                <span className="flex-1" />
                <button
                  className={BTN_BORDERED}
                  onClick={() => {
                    setSetupStage(null);
                    setWantOn(false);
                  }}
                  data-testid="sandbox-setup-not-now"
                >
                  {t("settingsx.sandbox.not_now")}
                </button>
                <button className={BTN_ACCENT} onClick={runSetup} data-testid="sandbox-setup-now">
                  {t("settingsx.sandbox.set_up_now")}
                </button>
              </div>
            </>
          ) : setupStage === "working" ? (
            <>
              <h3 className="text-heading font-semibold mb-1.5">{t("settingsx.sandbox.setup_working_title")}</h3>
              <p className="text-ui text-muted mb-3.5 leading-relaxed">{t("settingsx.sandbox.setup_working_intro")}</p>
              <div className="rounded-lg border border-line bg-paper px-3 py-2.5 text-ui text-muted flex items-center gap-2.5">
                <span className="w-4 h-4 rounded-full border-2 border-lineStrong border-t-accent animate-spin shrink-0" />
                {t("settingsx.sandbox.setup_waiting")}
              </div>
            </>
          ) : setupStage === "done" ? (
            <>
              <h3 className="text-heading font-semibold mb-1.5">{t("settingsx.sandbox.setup_done_title")}</h3>
              <p className="text-ui text-muted mb-3.5 leading-relaxed">{t("settingsx.sandbox.setup_done_intro")}</p>
              <div className="rounded-lg border border-okLine bg-okSoft px-3 py-2.5 text-ui text-ok leading-relaxed" data-testid="sandbox-setup-done-box">
                {t("settingsx.sandbox.setup_done_box", { checked: setupOutcome.checked || "" })}
              </div>
              <div className="flex justify-end mt-4">
                <button className={BTN_ACCENT} onClick={() => setSetupStage(null)} data-testid="sandbox-setup-done">
                  {t("settingsx.sandbox.done")}
                </button>
              </div>
            </>
          ) : (
            <>
              <h3 className="text-heading font-semibold mb-1.5">{t("settingsx.sandbox.setup_failed_title")}</h3>
              <p className="text-ui text-muted mb-3.5 leading-relaxed">{t("settingsx.sandbox.setup_failed_intro")}</p>
              <div className="rounded-lg border border-line bg-paper px-3 py-2.5 text-meta text-danger font-mono whitespace-pre-wrap break-words" data-testid="sandbox-setup-error">
                {setupOutcome.error}
              </div>
              <div className="flex justify-end gap-2 mt-4">
                <button
                  className={BTN_BORDERED}
                  onClick={() => {
                    setSetupStage(null);
                    setWantOn(false);
                  }}
                >
                  {t("settingsx.sandbox.close")}
                </button>
                <button className={BTN_ACCENT} onClick={() => setSetupStage("ask")}>
                  {t("settingsx.sandbox.try_again")}
                </button>
              </div>
            </>
          )}
        </Modal>
      ) : null}

      {/* The Remove setup confirmation */}
      {removing ? (
        <Modal testid="sandbox-remove-dialog">
          <h3 className="text-heading font-semibold mb-1.5">{t("settingsx.sandbox.remove_title")}</h3>
          <p className="text-ui text-muted mb-3.5 leading-relaxed">{t("settingsx.sandbox.remove_intro")}</p>
          <div className="text-label font-medium text-faint mb-1.5">{t("settingsx.sandbox.remove_what")}</div>
          <div className="grid gap-2.5">
            {SETUP_CHANGES.map((k) => (
              <div key={k} className="text-ui leading-relaxed">
                {t(`settingsx.sandbox.setup_change_${k}`)}
                <div className="text-meta text-muted">{t(`settingsx.sandbox.remove_change_${k}_desc`)}</div>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2 mt-4">
            <span className="text-meta text-faint max-w-[320px] leading-relaxed">{t("settingsx.sandbox.remove_keep_note")}</span>
            <span className="flex-1" />
            <button className={BTN_BORDERED} onClick={() => setRemoving(false)} disabled={removeBusy}>
              {t("settingsx.sandbox.cancel")}
            </button>
            <button className={BTN_ACCENT} onClick={runRemove} disabled={removeBusy} data-testid="sandbox-remove-confirm">
              {removeBusy ? t("settingsx.sandbox.setup_waiting") : t("settingsx.sandbox.remove")}
            </button>
          </div>
        </Modal>
      ) : null}
    </section>
  );
}

// "Choose sites…" (UX-053 v6): every site the allow list can hold, as a tick box in its
// group, none ticked until the user ticks some. Sites the user adds join the list and can
// be removed. Saving writes the ticked ones, "host:port".
function SitesDialog({
  t,
  ticked,
  catalogue,
  onCancel,
  onSave,
  error,
}: {
  t: T;
  ticked: string[];
  catalogue: { group: string; hosts: string[] }[];
  onCancel: () => void;
  onSave: (hosts: string[]) => void;
  error: string;
}) {
  const known = new Set(catalogue.flatMap((g) => g.hosts));
  const [on, setOn] = useState<Set<string>>(new Set(ticked));
  const [added, setAdded] = useState<string[]>(ticked.filter((h) => !known.has(h)));
  const [draft, setDraft] = useState("");
  const [bad, setBad] = useState(false);
  const flip = (hosts: string[], value: boolean) => {
    const next = new Set(on);
    hosts.forEach((h) => (value ? next.add(h) : next.delete(h)));
    setOn(next);
  };
  const add = () => {
    const host = cleanHost(draft);
    if (!host) {
      setBad(true);
      return;
    }
    setBad(false);
    setDraft("");
    if (!known.has(host) && !added.includes(host)) setAdded([...added, host]);
    flip([host], true);
  };
  const groups = [...(added.length ? [{ group: "added", hosts: added }] : []), ...catalogue];
  const result = [...catalogue.flatMap((g) => g.hosts), ...added].filter((h) => on.has(h));
  const bare = (h: string) => h.replace(/:443$/, "");
  return (
    <Modal testid="sandbox-sites-dialog" wide>
      <h3 className="text-heading font-semibold mb-1.5">{t("settingsx.sandbox.sites_title")}</h3>
      <p className="text-ui text-muted mb-3.5 leading-relaxed">{t("settingsx.sandbox.sites_intro")}</p>
      <div className="rounded-lg border border-line max-h-[330px] overflow-auto">
        {groups.map((g) => {
          const count = g.hosts.filter((h) => on.has(h)).length;
          const all = count === g.hosts.length;
          return (
            <div key={g.group} data-testid={`sandbox-sites-group-${g.group}`}>
              <label className="sticky top-0 flex items-center gap-2.5 px-3 py-2 bg-chrome border-b border-line text-meta font-semibold text-ink cursor-pointer">
                <input
                  type="checkbox"
                  checked={all}
                  ref={(el) => {
                    if (el) el.indeterminate = count > 0 && !all;
                  }}
                  onChange={() => flip(g.hosts, !all)}
                  data-testid={`sandbox-sites-group-${g.group}-box`}
                />
                {t(`settingsx.sandbox.site_group_${g.group}`)}
                <span className="ml-auto font-normal text-faint">{t("settingsx.sandbox.sites_count", { on: count, total: g.hosts.length })}</span>
              </label>
              {g.hosts.map((h) => (
                <label key={h} className="flex items-center gap-2.5 pl-9 pr-3 py-1.5 border-b border-line text-meta font-mono text-ink cursor-pointer" data-testid={`sandbox-site-${h}`}>
                  <input type="checkbox" checked={on.has(h)} onChange={() => flip([h], !on.has(h))} />
                  {bare(h)}
                  {g.group === "added" ? (
                    <button
                      type="button"
                      className="ml-auto font-sans text-muted hover:text-ink"
                      onClick={(e) => {
                        e.preventDefault();
                        setAdded(added.filter((x) => x !== h));
                        flip([h], false);
                      }}
                    >
                      {t("settingsx.sandbox.remove")}
                    </button>
                  ) : null}
                </label>
              ))}
            </div>
          );
        })}
      </div>
      <div className="flex gap-2 mt-2.5">
        <input
          className={INPUT + " font-mono" + (bad ? " border-danger" : "")}
          value={draft}
          placeholder={t("settingsx.sandbox.sites_placeholder")}
          onChange={(e) => {
            setDraft(e.target.value);
            setBad(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
          }}
          data-testid="sandbox-site-input"
        />
        <button className={BTN_BORDERED} onClick={add} disabled={!draft.trim()} data-testid="sandbox-site-add">
          {t("settingsx.sandbox.add_button")}
        </button>
      </div>
      {bad ? <div className="text-meta text-danger mt-1">{t("settingsx.sandbox.sites_bad")}</div> : null}
      {error ? <div className="text-meta text-danger mt-1">{error}</div> : null}
      <div className="flex items-center gap-2 mt-4">
        <span className="text-meta text-faint" data-testid="sandbox-sites-total">
          {t("settingsx.sandbox.sites_total", { count: result.length })}
        </span>
        <span className="flex-1" />
        <button className={BTN_BORDERED} onClick={onCancel}>
          {t("settingsx.sandbox.cancel")}
        </button>
        <button className={BTN_ACCENT} onClick={() => onSave(result)} data-testid="sandbox-sites-save">
          {t("settingsx.sandbox.save")}
        </button>
      </div>
    </Modal>
  );
}

// The same rule as the server's network_profiles.clean_host: "host:port", 443 when bare.
export function cleanHost(text: string): string {
  let s = text.trim().toLowerCase().replace(/\.$/, "");
  if (s.includes("://")) s = s.split("://", 2)[1].split("/", 1)[0];
  let host = s;
  let port = "443";
  const at = s.lastIndexOf(":");
  if (at >= 0) {
    host = s.slice(0, at);
    port = s.slice(at + 1);
  }
  const name = host.startsWith("*.") ? host.slice(2) : host;
  const labels = name.split(".");
  if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535 || labels.length < 2 || !labels.every((l) => /^[a-z0-9-]+$/.test(l))) return "";
  return `${host}:${Number(port)}`;
}

type RowState = "ok" | "bad" | "run" | "todo" | "wait";
type Row = SandboxReadinessStep & { state?: SandboxSetupRowState };

// OpenShell's setup (OPE-207, UX-053 v6): three rows that stay put from start to finish.
// Two are checks the user fixes (Docker running, OpenShell installed); the third is what
// the app does (the folder setting, the image download, choosing OpenShell), with the
// download's progress. The server's finer steps are folded into the third row.
function OpenShellDialog({
  t,
  platform,
  readiness,
  job,
  ready,
  onStart,
  onCancel,
  onCheck,
  onClose,
  copy,
  copied,
}: {
  t: T;
  platform: string;
  readiness: SandboxReadiness | null;
  job: SandboxSetupState | null;
  ready: boolean;
  onStart: () => void;
  onCancel: () => void;
  onCheck: () => void;
  onClose: () => void;
  copy: (text: string) => void;
  copied: string;
}) {
  const running = job?.status === "running";
  const rows: Row[] = job && job.status !== "idle" && job.rows.length ? job.rows : readiness?.steps ?? [];
  const byKey = (key: string) => rows.find((r) => r.key === key);
  const good = (r?: Row) => Boolean(r) && (r!.state ? r!.state === "ok" || r!.state === "fixed" : r!.ok);
  const docker = byKey("docker");
  const kernel = byKey("docker_landlock"); // a Mac: does Docker Desktop's Linux kernel have Landlock
  const hostNet = byKey("docker_host_network"); // a Mac: is Docker Desktop's host networking on
  const openshell = byKey("openshell");
  const rest = rows.filter((r) => r.key !== "docker" && r.key !== "docker_landlock" && r.key !== "docker_host_network" && r.key !== "openshell");
  const blocked = rest.find((r) => r.state === "needs_you" || r.state === "failed");
  const dockerOk = good(docker) && (!kernel || good(kernel)) && (!hostNet || good(hostNet));
  const checksOk = dockerOk && good(openshell);
  const allOk = rows.length > 0 && rows.every((r) => good(r));
  const done = job?.status === "done" || (!job && ready && allOk);
  const mark = (r?: Row): RowState => (!r ? "wait" : good(r) ? "ok" : "bad");
  const third: RowState = done || (rows.length > 0 && rest.every((r) => good(r)) && checksOk) ? "ok" : blocked ? "bad" : running ? "run" : checksOk ? "todo" : "wait";
  const elapsed = (s: number) => (s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`);
  const mac = platform === "darwin";
  const progress = running && job?.progress;
  const title = done ? t("settingsx.sandbox.os_done_title") : running ? t("settingsx.sandbox.os_running_title") : t("settingsx.sandbox.os_title");
  const intro = done ? t(mac ? "settingsx.sandbox.os_done_intro_mac" : "settingsx.sandbox.os_done_intro") : t("settingsx.sandbox.os_intro");
  const Mark = ({ state }: { state: RowState }) =>
    state === "ok" ? (
      <span className="w-[18px] h-[18px] rounded-full bg-ok text-white text-[11px] flex items-center justify-center shrink-0 mt-px">✓</span>
    ) : state === "bad" ? (
      <span className="w-[18px] h-[18px] rounded-full border border-warnInk/40 bg-warnSoft text-warnInk text-[11px] font-bold flex items-center justify-center shrink-0 mt-px">!</span>
    ) : state === "run" ? (
      <span className="w-[18px] h-[18px] rounded-full border-2 border-lineStrong border-t-accent animate-spin shrink-0 mt-px" />
    ) : (
      <span className="w-[18px] h-[18px] rounded-full border-[1.5px] border-lineStrong shrink-0 mt-px" />
    );
  const line = (key: string, state: RowState, text: string, detail?: React.ReactNode) => (
    <li className="flex items-start gap-3 px-3.5 py-3" data-testid={`sandbox-setup-row-${key}`} data-state={state}>
      <Mark state={state} />
      <span className="flex-1 min-w-0">
        <span className={"block text-ui " + (state === "wait" ? "text-faint" : "text-ink")}>{text}</span>
        {detail}
      </span>
    </li>
  );
  // The desktop webview opens nothing by itself: links go to the browser through the opener.
  const link = (href: string, label: string, testid: string) => (
    <a
      className="text-accent"
      href={href}
      onClick={(e) => {
        e.preventDefault();
        openExternal(href);
      }}
      data-testid={testid}
    >
      {label}
    </a>
  );
  const blockedCommand = blocked?.command;
  return (
    <Modal testid="sandbox-openshell-dialog">
      <h3 className="text-heading font-semibold mb-1.5">{title}</h3>
      <p className="text-ui text-muted mb-3.5 leading-relaxed" data-testid={job && job.status !== "idle" ? `sandbox-setup-${job.status}` : undefined}>
        {intro}
      </p>
      {!readiness && !job ? (
        <div className="rounded-lg border border-line bg-paper px-3 py-2.5 text-ui text-muted flex items-center gap-2.5">
          <span className="w-4 h-4 rounded-full border-2 border-lineStrong border-t-accent animate-spin shrink-0" />
          {t("settingsx.sandbox.readiness_loading")}
        </div>
      ) : (
        <ul className="rounded-lg border border-line divide-y divide-line" data-testid="sandbox-readiness">
          {line(
            "docker",
            !docker ? "wait" : dockerOk ? "ok" : "bad",
            t(mac ? "settingsx.sandbox.os_row_docker_mac" : "settingsx.sandbox.os_row_docker"),
            docker && good(docker) && kernel && !good(kernel) ? (
              <span className="block text-meta text-muted mt-0.5" data-testid="sandbox-setup-landlock">
                <span className="block">
                  {t("settingsx.sandbox.os_docker_update")}{" "}
                  {link(kernel.docs || "https://docs.docker.com/desktop/setup/install/mac-install/", t("settingsx.sandbox.get_docker_mac"), "sandbox-setup-docs-landlock")}
                </span>
                <span className="block">{t("settingsx.sandbox.os_docker_update_why")}</span>
              </span>
            ) : docker && good(docker) && hostNet && !good(hostNet) ? (
              <span className="block text-meta text-muted mt-0.5" data-testid="sandbox-setup-hostnet">
                <span className="block">
                  {t("settingsx.sandbox.os_docker_hostnet")}{" "}
                  {link(hostNet.docs || "https://docs.docker.com/engine/network/drivers/host/#docker-desktop", t("settingsx.sandbox.docker_hostnet_link"), "sandbox-setup-docs-hostnet")}
                </span>
                <span className="block">{t("settingsx.sandbox.os_docker_hostnet_where")}</span>
                <span className="block">{t("settingsx.sandbox.os_docker_hostnet_why")}</span>
              </span>
            ) : docker && !good(docker) ? (
              <span className="block text-meta text-muted mt-0.5">
                {t(mac ? "settingsx.sandbox.os_docker_fix_mac" : "settingsx.sandbox.os_docker_fix")}{" "}
                {docker.docs ? link(docker.docs, t(mac ? "settingsx.sandbox.get_docker_mac" : "settingsx.sandbox.get_docker"), "sandbox-setup-docs-docker") : null}
              </span>
            ) : null,
          )}
          {line(
            "openshell",
            mark(openshell),
            t("settingsx.sandbox.os_row_openshell"),
            openshell && !good(openshell) ? (
              <span className="block text-meta text-muted mt-0.5">
                {openshell.hint ? openshell.hint + ". " : ""}
                {t("settingsx.sandbox.os_openshell_fix")}{" "}
                {openshell.docs ? link(openshell.docs, t("settingsx.sandbox.os_openshell_guide"), "sandbox-setup-docs-openshell") : null}
              </span>
            ) : null,
          )}
          {line(
            "setup",
            third,
            t("settingsx.sandbox.os_row_setup"),
            third === "bad" && blocked ? (
              <span className="block mt-0.5">
                <span className="block text-meta text-muted">{blocked.hint || blocked.what}</span>
                {blockedCommand ? (
                  <span className="mt-1.5 flex items-start gap-2 rounded-lg border border-line bg-paper px-2.5 py-1.5" data-testid="sandbox-setup-command">
                    <code className="text-meta font-mono text-ink break-all flex-1 min-w-0">{blockedCommand}</code>
                    <button className="text-meta text-accent shrink-0" onClick={() => copy(blockedCommand)}>
                      {copied === blockedCommand ? t("settingsx.sandbox.copied") : t("settingsx.sandbox.copy")}
                    </button>
                  </span>
                ) : null}
              </span>
            ) : progress && progress.layers_total ? (
              <span className="block mt-0.5" data-testid="sandbox-download-progress">
                <span className="block text-meta text-muted">
                  {t("settingsx.sandbox.download_progress", { done: progress.layers_done, total: progress.layers_total, elapsed: elapsed(progress.elapsed_s) })}
                </span>
                <span className="block h-1.5 rounded bg-line overflow-hidden mt-2">
                  <span className="block h-full bg-accent transition-all" style={{ width: `${Math.round((100 * progress.layers_done) / progress.layers_total)}%` }} />
                </span>
              </span>
            ) : third === "ok" ? null : (
              <span className="block text-meta text-muted mt-0.5">{t("settingsx.sandbox.os_row_setup_desc")}</span>
            ),
          )}
        </ul>
      )}
      <div className="flex items-center gap-2 mt-4">
        <span className="flex-1" />
        {done ? (
          <button className={BTN_ACCENT} onClick={onClose} data-testid="sandbox-openshell-done">
            {t("settingsx.sandbox.done")}
          </button>
        ) : running ? (
          <>
            <button className={BTN_BORDERED} onClick={onCancel} data-testid="sandbox-setup-cancel">
              {t("settingsx.sandbox.os_cancel_setup")}
            </button>
            <button className={BTN_BORDERED} onClick={onClose} data-testid="sandbox-openshell-hide">
              {t("settingsx.sandbox.os_hide")}
            </button>
          </>
        ) : (
          <>
            <button className={BTN_BORDERED} onClick={onClose} data-testid="sandbox-openshell-close">
              {t("settingsx.sandbox.cancel")}
            </button>
            {checksOk && third !== "bad" ? (
              <button className={BTN_ACCENT} onClick={onStart} data-testid="sandbox-setup-start">
                {t("settingsx.sandbox.set_up")}
              </button>
            ) : (
              <button className={BTN_BORDERED} onClick={onCheck} disabled={!readiness && !job} data-testid="sandbox-setup-check">
                {t("settingsx.sandbox.setup_again")}
              </button>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

function validPath(path: string): boolean {
  return (path.startsWith("~/") || path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path)) && path.length >= 3;
}

// Add or edit one entry: a file or a folder under the home folder, what is in it, the
// hosts its tool needs, and what it lets the agent do.
export function CredentialEditor({
  entry,
  titleOf,
  doesOf,
  onCancel,
  onSave,
}: {
  entry: SandboxCredentialEntry | null;
  titleOf?: (c: SandboxCredentialEntry) => string;
  doesOf?: (c: SandboxCredentialEntry) => string;
  onCancel: () => void;
  onSave: (row: SandboxCredentialEntry) => void;
}) {
  const { t } = useTranslation();
  const [title, setTitle] = useState(entry ? (titleOf ? titleOf(entry) : entry.title ?? "") : "");
  const [path, setPath] = useState(entry?.path ?? "~/");
  const [hosts, setHosts] = useState((entry?.hosts ?? []).join("\n"));
  const [does, setDoes] = useState(entry ? (doesOf ? doesOf(entry) : entry.does ?? "") : "");
  const [label, setLabel] = useState<"credential" | "configuration">(entry?.label === "configuration" ? "configuration" : "credential");
  const slug = entry?.name || title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const valid = Boolean(slug) && validPath(path);
  return (
    <Modal testid="sandbox-credential-editor" wide>
      <h3 className="text-heading font-semibold mb-1.5">{entry ? t("settingsx.sandbox.file_title_edit") : t("settingsx.sandbox.file_title")}</h3>
      <p className="text-ui text-muted mb-3.5 leading-relaxed">{t("settingsx.sandbox.file_intro")}</p>
      <div className="grid gap-3">
        <label className="block">
          <span className="block text-meta text-muted mb-1">
            {t("settingsx.sandbox.field_path")} <span className="text-faint">{t("settingsx.sandbox.field_path_hint")}</span>
          </span>
          <span className="flex gap-2">
            <input className={INPUT + " font-mono"} value={path} onChange={(e) => setPath(e.target.value)} data-testid="sandbox-credential-path" />
            <button
              className={BTN_BORDERED}
              onClick={async (e) => {
                e.preventDefault();
                const picked = await chooseFolder();
                if (picked) setPath(picked);
              }}
            >
              {t("settingsx.sandbox.browse")}
            </button>
          </span>
        </label>
        <label className="block">
          <span className="block text-meta text-muted mb-1">{t("settingsx.sandbox.field_title")}</span>
          <input className={INPUT + " w-full"} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("settingsx.sandbox.field_title_example")} data-testid="sandbox-credential-title" />
        </label>
        <div>
          <span className="block text-meta text-muted mb-1">{t("settingsx.sandbox.field_label")}</span>
          <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label={t("settingsx.sandbox.field_label")}>
            {(["credential", "configuration"] as const).map((k) => (
              <label key={k} className={"flex items-start gap-2.5 rounded-lg border px-3 py-2 cursor-pointer " + (label === k ? "border-accent bg-accentSoft" : "border-line")}>
                <input type="radio" name="sandbox-credential-label" className="mt-1" checked={label === k} onChange={() => setLabel(k)} data-testid={`sandbox-credential-label-${k}`} />
                <span>
                  <span className="block text-ui text-ink font-medium">{t(`settingsx.sandbox.label_${k}_title`)}</span>
                  <span className="block text-meta text-muted">{t(`settingsx.sandbox.label_${k}_desc`)}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
        <label className="block">
          <span className="block text-meta text-muted mb-1">{t("settingsx.sandbox.field_hosts")}</span>
          <textarea className={INPUT + " w-full font-mono"} rows={2} value={hosts} onChange={(e) => setHosts(e.target.value)} placeholder={t("settingsx.sandbox.field_hosts_example")} />
        </label>
        <label className="block">
          <span className="block text-meta text-muted mb-1">{t("settingsx.sandbox.field_does")}</span>
          <input className={INPUT + " w-full"} value={does} onChange={(e) => setDoes(e.target.value)} placeholder={t("settingsx.sandbox.field_does_example")} />
        </label>
      </div>
      <div className="flex items-center gap-2 mt-4">
        <span className="text-meta text-faint max-w-[340px]">{t("settingsx.sandbox.editor_note")}</span>
        <span className="flex-1" />
        <button className={BTN_BORDERED} onClick={onCancel}>
          {t("settingsx.sandbox.cancel")}
        </button>
        <button
          className={BTN_ACCENT}
          disabled={!valid}
          data-testid="sandbox-credential-save"
          onClick={() =>
            onSave({
              name: slug,
              title: title && title !== (entry && titleOf ? titleOf({ ...entry, title: undefined }) : "") ? title : entry?.title,
              path,
              hosts: hosts
                .split(/[\s,]+/)
                .map((h) => cleanHost(h) || h.trim())
                .filter(Boolean),
              does: does && does !== (entry && doesOf ? doesOf({ ...entry, does: undefined }) : "") ? does : entry?.does,
              label,
              enabled: entry?.enabled ?? true,
            })
          }
        >
          {entry ? t("settingsx.sandbox.save") : t("settingsx.sandbox.add_button")}
        </button>
      </div>
    </Modal>
  );
}
