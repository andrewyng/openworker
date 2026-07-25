import { useEffect, useState } from "react";
import {
  getSettings,
  getTrustedWorkspaces,
  setOnboarded,
  setPdfSettings,
  setScratchBase,
  setSessionsPeek,
  setWorkspaceTrusted,
  type ModelSettings,
  type PdfSettings,
  type WorkspaceCommandTrust,
} from "../api";
import {
  getAutostart,
  getKeepAwake,
  checkForUpdate,
  installUpdate,
  isTauri,
  pickFolder,
  setAutostart,
  setKeepAwake,
} from "../tauri";
import { useThemePref } from "../theme";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";
import { ModelsTab } from "./ManageTabs";
import { GalleryModal } from "./GalleryModal";
import { PersonasTab } from "./PersonasTab";
import { showPersonas } from "../flags";
import { VoiceInputSection } from "./VoiceInputSection";

// Settings, restructured (Option 2) into a full-page surface that mirrors IntegrationsView's shell:
// a left sub-nav (Appearance · Files · Models · Personas) + centered panel, replacing the old
// top-tab ManageModal. Local/app concerns live here; anything external (Connectors, Messaging, MCP,
// Activity) stays under Integrations. Appearance + Files are re-skinned to the mock's Tailwind idiom;
// Models + Personas host the existing tab components inside the page shell (field re-skin to follow).
// "appearance" is the General tab's stable key — callers deep-link with it, so the
// rename (UX-021) changed only the label. "files" folded into General as a card.
type SetTab = "appearance" | "models" | "voice" | "personas";

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-[12.5px] font-medium text-ink";
const FIELD_HELP = "text-[12px] text-muted mt-1.5 leading-relaxed";
const INPUT =
  "flex-1 min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const BTN_ACCENT =
  "text-[12.5px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-[12.5px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";

const SET_TABS: {
  key: SetTab;
  label: string;
  icon: "sliders" | "code" | "mic" | "sparkle";
}[] = [
  { key: "appearance", label: "General", icon: "sliders" },
  { key: "models", label: "Models", icon: "code" },
  { key: "voice", label: "Voice input", icon: "mic" },
  { key: "personas", label: "Personas", icon: "sparkle" },
];

export function SettingsView({
  initialTab,
  onOpenPersona,
}: {
  initialTab?: SetTab;
  onOpenPersona?: (id: string) => void;
}) {
  // Personas is flag-gated (hidden for launch) — filter the tab AND coerce a stale
  // deep-link to it (openSettings("personas") callers) so the page never opens on a
  // section with no nav entry.
  const personas = showPersonas();
  const tabs = personas
    ? SET_TABS
    : SET_TABS.filter((t) => t.key !== "personas");
  const wanted =
    initialTab && (personas || initialTab !== "personas")
      ? initialTab
      : "appearance";
  const [tab, setTab] = useState<SetTab>(wanted);

  return (
    <main className="flex-1 min-w-0 flex bg-paper">
      <nav className="page-subnav w-[208px] shrink-0 border-r border-line bg-panel/40 px-3 py-4">
        <div className="px-2 text-[13.5px] font-semibold mb-3 flex items-center gap-2">
          <Icon name="gear" size={16} /> Settings
        </div>
        {tabs.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              className={
                "w-full text-left px-2.5 py-2 rounded-lg text-[13px] flex items-center gap-2 " +
                (active
                  ? "bg-paper text-accent font-medium"
                  : "text-muted hover:bg-paper hover:text-ink")
              }
              onClick={() => setTab(t.key)}
            >
              <Icon name={t.icon} size={15} /> {t.label}
            </button>
          );
        })}
      </nav>

      <div className="flex-1 min-w-0 overflow-y-auto hairline-scroll">
        <div className="max-w-3xl mx-auto px-7 py-6">
          {tab === "appearance" ? (
            <AppearanceSection />
          ) : tab === "models" ? (
            <section>
              <PanelHead
                title="Models"
                sub="Providers and the models offered in the composer's picker. Keys are stored only on this computer."
              />
              <ModelsTab />
              {/* Token savings is model-spend behavior, so it lives here (UX-021),
                  not under General. */}
              <div className="mt-6">
                <TokenSavingsCard />
              </div>
            </section>
          ) : tab === "voice" ? (
            <VoiceInputSection />
          ) : (
            <PersonasSection onOpenPersona={onOpenPersona} />
          )}
        </div>
      </div>
    </main>
  );
}

// -- Personas: installed/enabled/delete management, the dir/Git importer, and the
// entry point to the Persona Gallery (a screen-sized modal — installs finish back
// here, disabled pending consent; a gallery install re-mounts the list in place).
function PersonasSection({
  onOpenPersona,
}: {
  onOpenPersona?: (id: string) => void;
}) {
  const [galleryBump, setGalleryBump] = useState(0);
  const [galleryOpen, setGalleryOpen] = useState(false);

  return (
    <section>
      <PanelHead
        title="Personas"
        sub="Which coworkers are enabled and shown in the picker, plus installing new persona bundles."
      />
      <PersonasTab key={galleryBump} onOpenPersona={onOpenPersona} />
      <button
        className="mt-6 w-full rounded-xl2 border border-line bg-panel px-4 py-3.5 flex items-center gap-3 text-left hover:border-lineStrong"
        data-testid="gallery-link"
        onClick={() => setGalleryOpen(true)}
      >
        <Icon name="sparkle" size={16} className="text-accent shrink-0" />
        <span className="min-w-0 flex-1">
          <span className="block text-[13.5px] font-medium">
            Browse the Persona Gallery
          </span>
          <span className="block text-[12px] text-muted">
            Curated coworkers from the OpenWorker team — see what each can do
            before installing.
          </span>
        </span>
        <span className="text-[12.5px] text-accent shrink-0">Open →</span>
      </button>
      {galleryOpen && (
        <GalleryModal
          onClose={() => setGalleryOpen(false)}
          onInstalled={() => setGalleryBump((b) => b + 1)}
        />
      )}
    </section>
  );
}

// -- Appearance + app behaviour ------------------------------------------------
function AppearanceSection() {
  const [theme, setTheme] = useThemePref();
  const [autostart, setAuto] = useState(false);
  const [keepAwake, setKeep] = useState(false);
  const desktop = isTauri();

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

  return (
    <section>
      <PanelHead
        title="General"
        sub="How OpenWorker looks and behaves on this machine."
      />

      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>Theme</div>
        <div className="seg mt-2.5" role="radiogroup" aria-label="Appearance">
          {(["light", "dark", "auto"] as const).map((p) => (
            <button
              key={p}
              className={p === theme ? "active" : ""}
              onClick={() => setTheme(p)}
            >
              {p === "light" ? "Light" : p === "dark" ? "Dark" : "Auto"}
            </button>
          ))}
        </div>
        <div className={FIELD_HELP}>
          Auto follows your Mac&rsquo;s appearance.
        </div>
      </div>

      <SidebarCard />

      <FilesCard />

      <TrustedWorkspacesCard />

      {desktop && (
        <div className={CARD + " p-4"}>
          <div className={FIELD_LABEL + " mb-2.5"}>Always-on</div>
          <label className="flex items-start gap-3 py-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={autostart}
              onChange={(e) => toggleAuto(e.target.checked)}
            />
            <span>
              <span className="block text-[13px] text-ink">Open at login</span>
              <span className="block text-[12px] text-muted">
                Launch OpenWorker automatically when you sign in.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-3 py-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={keepAwake}
              onChange={(e) => toggleKeep(e.target.checked)}
            />
            <span>
              <span className="block text-[13px] text-ink">
                Keep this system awake
              </span>
              <span className="block text-[12px] text-muted">
                Prevent idle sleep so scheduled tasks fire on time.
              </span>
            </span>
          </label>
        </div>
      )}

      {/* One card for the app-lifecycle actions (UX-021): the onboarding replay (§24 —
          every build, the browser dev shell runs the same first-run flow) and, on
          desktop, the manual update check (launch also checks automatically). */}
      <div className={CARD + " p-4 mt-4"}>
        <div className={FIELD_LABEL + " mb-2"}>Setup &amp; updates</div>
        <div className="flex items-center gap-2">
          <button className={BTN_BORDERED} onClick={runSetupAgain}>
            Run setup again
          </button>
          {desktop && <UpdateInline />}
        </div>
        <div className={FIELD_HELP}>
          Replays the first-run setup: model, first automation, tips.
        </div>
      </div>
    </section>
  );
}

function TrustedWorkspacesCard() {
  const [workspaces, setWorkspaces] = useState<WorkspaceCommandTrust[] | null>(
    null,
  );

  const refresh = () =>
    getTrustedWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspaces([]));

  useEffect(() => {
    refresh();
  }, []);

  const revoke = async (path: string) => {
    if (!window.confirm(`Revoke command trust for ${path}?`)) return;
    await setWorkspaceTrusted(path, false);
    refresh();
  };

  return (
    <div className={CARD + " p-4 mb-4"} data-testid="trusted-workspaces-card">
      <div className={FIELD_LABEL}>Trusted workspaces</div>
      <div className={FIELD_HELP}>
        Trusted projects may manage their command allowances in
        .coworker/config.toml.
      </div>
      {workspaces === null ? (
        <div className="text-[12px] text-muted mt-3">Loading…</div>
      ) : workspaces.length === 0 ? (
        <div className="text-[12px] text-muted mt-3">
          No workspaces are trusted.
        </div>
      ) : (
        <div className="mt-3 divide-y divide-line">
          {workspaces.map((workspace) => (
            <div
              key={workspace.workspace}
              className="py-2.5 flex items-start gap-3"
            >
              <div className="min-w-0 flex-1">
                <div className="text-[12.5px] text-ink break-all">
                  {workspace.workspace}
                </div>
                <div className="text-[11.5px] text-muted mt-0.5">
                  {workspace.requested_commands.length
                    ? `${workspace.requested_commands.length} project command allowance${workspace.requested_commands.length === 1 ? "" : "s"}`
                    : "No project command allowances currently declared"}
                  {!workspace.exists ? " · Folder unavailable" : ""}
                </div>
              </div>
              <button
                className="text-[12px] text-red-600 px-2 py-1"
                onClick={() => void revoke(workspace.workspace)}
              >
                Revoke
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function UpdateInline() {
  const [state, setState] = useState<
    "idle" | "checking" | "none" | "found" | "installing" | "error"
  >("idle");
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
        <button
          className={BTN_BORDERED}
          onClick={install}
          data-testid="settings-update-install"
        >
          Update to v{version} and restart
        </button>
      ) : (
        <button
          className={BTN_BORDERED}
          onClick={check}
          disabled={state === "checking" || state === "installing"}
          data-testid="settings-update-check"
        >
          {state === "checking" ? "Checking…" : "Check for updates"}
        </button>
      )}
      {(state === "none" || state === "error" || state === "installing") && (
        <span className="text-[12px] text-muted">
          {state === "none"
            ? "You're on the latest version."
            : state === "error"
              ? "Couldn't check right now — try again later."
              : "Downloading — OpenWorker restarts by itself when it's ready."}
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
// Auto-compaction of long histories is a planned follow-up (punchlist §7) — until
// then this card is the user's dial: attach thresholds + the fallback for models
// without native PDF support.
function TokenSavingsCard() {
  const [pdf, setPdf] = useState<PdfSettings | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) =>
        setPdf({
          pdf_fallback: s.pdf_fallback || "text",
          pdf_max_pages: s.pdf_max_pages || 20,
          pdf_max_mb: s.pdf_max_mb || 10,
        }),
      )
      .catch(() =>
        setPdf({ pdf_fallback: "text", pdf_max_pages: 20, pdf_max_mb: 10 }),
      );
  }, []);

  const save = async (patch: Partial<PdfSettings>) => {
    setPdf((p) => (p ? { ...p, ...patch } : p));
    await setPdfSettings(patch);
  };

  if (!pdf) return null;
  return (
    <div className={CARD + " p-4 mb-4"} data-testid="token-savings-card">
      <div className={FIELD_LABEL}>Token savings</div>
      <div className={FIELD_HELP}>
        PDF attachments travel with every turn of a conversation, so large
        documents multiply what you spend on tokens.
      </div>

      <div className="mt-3 text-[13px] text-ink">
        PDFs on models without native PDF support
      </div>
      <div
        className="seg mt-2"
        role="radiogroup"
        aria-label="PDF fallback"
        data-testid="pdf-fallback"
      >
        <button
          className={pdf.pdf_fallback === "text" ? "active" : ""}
          onClick={() => save({ pdf_fallback: "text" })}
        >
          Extract text
        </button>
        <button
          className={pdf.pdf_fallback === "images" ? "active" : ""}
          onClick={() => save({ pdf_fallback: "images" })}
        >
          Send page images
        </button>
      </div>
      <div className={FIELD_HELP}>
        Claude, GPT and Gemini read PDFs natively — this only applies to models
        that don&rsquo;t (GLM, Kimi, DeepSeek, local models…). Text extraction
        is cheapest; page images cost more tokens and need a vision-capable
        model.
      </div>

      <div className="mt-3 flex items-center gap-5">
        <label className="flex items-center gap-2.5">
          <span className="text-[13px] text-ink">Max pages</span>
          <input
            type="number"
            min={1}
            max={100}
            value={pdf.pdf_max_pages}
            data-testid="pdf-max-pages"
            className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
            onChange={(e) =>
              save({
                pdf_max_pages: Math.max(
                  1,
                  Math.min(Number(e.target.value) || 20, 100),
                ),
              })
            }
          />
        </label>
        <label className="flex items-center gap-2.5">
          <span className="text-[13px] text-ink">Max size</span>
          <input
            type="number"
            min={1}
            max={10}
            value={pdf.pdf_max_mb}
            data-testid="pdf-max-mb"
            className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
            onChange={(e) =>
              save({
                pdf_max_mb: Math.max(
                  1,
                  Math.min(Number(e.target.value) || 10, 10),
                ),
              })
            }
          />
          <span className="text-[12.5px] text-muted">MB</span>
        </label>
      </div>
      <div className={FIELD_HELP}>
        PDFs over these limits are not attached — you&rsquo;ll see a notice in
        the composer instead.
      </div>
    </div>
  );
}

function SidebarCard() {
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
      <div className={FIELD_LABEL}>Sidebar</div>
      <label className="flex items-center gap-3 mt-2.5">
        <span className="text-[13px] text-ink">
          Conversations shown per coworker
        </span>
        <input
          type="number"
          min={1}
          max={50}
          value={peek}
          className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
          onChange={(e) => save(Number(e.target.value))}
        />
      </label>
      <div className={FIELD_HELP}>
        Longer lists collapse behind &ldquo;Show more&rdquo;. Applies per
        coworker and per project.
      </div>
    </div>
  );
}

// -- Files (scratch location) — one card inside General (UX-021: a single option
// doesn't earn its own tab) -----------------------------------------------------
function FilesCard() {
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
      setScratchMsg("Saved. New conversations will use this location.");
      refresh();
    } else {
      setScratchMsg(res.error || "Could not use that location.");
    }
  };
  const browseScratch = async () => {
    const picked = await pickFolder();
    if (picked) setScratchDraft(picked);
  };

  if (!settings) return null;

  return (
    <div className={CARD + " p-4 mb-4"}>
      <div className={FIELD_LABEL}>Files</div>
      <div className="flex items-center gap-2 mt-2.5">
        <input
          className={INPUT}
          type="text"
          placeholder="~/OpenWorker"
          value={scratchDraft}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => setScratchDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && saveScratch()}
        />
        {desktop && (
          <button
            className={BTN_BORDERED}
            onClick={browseScratch}
            title="Pick a folder"
          >
            Browse
          </button>
        )}
        <button
          className={BTN_ACCENT}
          onClick={saveScratch}
          disabled={!scratchDraft.trim()}
        >
          Save
        </button>
      </div>
      <div className={FIELD_HELP}>
        Each conversation gets its own folder under this location. Existing
        conversations keep their current folder; you can grant access to more
        folders inside any conversation.
      </div>
      {scratchMsg && (
        <div className="text-[12.5px] text-muted mt-2.5">{scratchMsg}</div>
      )}
    </div>
  );
}
