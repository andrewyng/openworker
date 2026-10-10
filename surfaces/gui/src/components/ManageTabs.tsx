import { useEffect, useState } from "react";
import { getI18n, useTranslation } from "react-i18next";
import {
  addTelegramApprovalOwner,
  allowUser,
  connectConnector,
  connectManaged,
  connectMcpBacked,
  disallowUser,
  getSettings,
  getSubscriptions,
  removeModel,
  removeTelegramApprovalOwner,
  resolveUnauthorized,
  unsubscribeChannel,
  setDefaultModel,
  updateConnectorTools,
  type CloudStatus,
  type Connector,
  type Subscription,
  type ModelSettings,
  type ProviderInfo,
} from "../api";
import { CloudSignInInline, CloudStatusPending } from "./connectors/CloudSignIn";
import { Icon } from "./Icon";
import { ModelChecklist } from "./ModelChecklist";
import { LocalModelsTable, YourSystem } from "./models/LocalModels";
import { PickModelDialog } from "./models/PickModelDialog";
import { ProviderGroups } from "./models/ProviderGroups";
import { ProviderForm, useProviderSetup } from "../providers/ProviderSetup";
import { WalletChips } from "./WalletChips";

// "2h ago"-style label for the providers' Last-used line (null when never used).
const relTime = (epoch?: number | null): string | null => {
  if (!epoch) return null;
  const t = getI18n().getFixedT(null, "translation");
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (secs < 90) return t("manage.reltime_just_now");
  const mins = Math.floor(secs / 60);
  if (mins < 60) return t("manage.reltime_min", { n: mins });
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return t("manage.reltime_hour", { n: hrs });
  return t("manage.reltime_day", { n: Math.floor(hrs / 24) });
};

// Shared tab bodies for the Settings and Integrations pages (the old top-tab ManageModal was retired
// when Settings/Activity became full-page surfaces): ModelsTab → Settings ▸ Models; ConnectorsTab →
// Integrations ▸ Connectors (the MCP tab retired into the Connectors page, UX-034).
const SEC_H = "text-label text-faint font-medium";
const BTN_BORDERED =
  "text-ui px-3 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";
const BTN_ACCENT = "text-ui px-3 py-1.5 rounded-lg bg-accent text-white shrink-0 disabled:opacity-50";

/** Two-letter initials for a chip/avatar (first+last word, else first two chars). */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// "Remove key…" under a provider's form. Only for a key the app itself stores: a key
// that comes from the provider's environment variable (key_source "env") cannot be
// removed here, so the button is left out and the env note below the form says why (#742).
export function ProviderKeyFooter({
  info,
  credentialed,
  onRemove,
}: {
  info: ProviderInfo | null | undefined;
  credentialed: boolean;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  if (!credentialed || info?.key_source === "env") return null;
  return (
    <button
      className="text-ui text-danger/80 hover:text-danger hover:underline underline-offset-2"
      data-testid="set-remove-key"
      onClick={() => {
        if (window.confirm(t("manage.remove_key_confirm", { title: info?.title || "" }))) onRemove();
      }}
    >
      {t("manage.remove_key")}
    </button>
  );
}

// -- Configure Models tab (UX-021: the shared provider gallery + key form) ----
// Settings ▸ Models reuses onboarding §39's ProviderCards/ProviderForm so the two
// surfaces can't drift. Settings-only extras: per-card "used Nh ago", a "Remove
// key…" affordance, the global composer-picker card (gallery view), and the
// per-provider ModelChecklist / read-only model preview (form view).
export function ModelsTab() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  // The Pick a model dialog (UX-055): "" closed, "+" picking, else the model to configure.
  const [dialog, setDialog] = useState<string>("");
  const refreshSettings = () => getSettings().then(setSettings).catch(() => setSettings(null));
  const ps = useProviderSetup({ onSaved: refreshSettings });
  useEffect(() => {
    refreshSettings();
    // The composer's "Pick or configure a model…" lands here with the dialog up.
    try {
      if (sessionStorage.getItem("ow:pick-model") === "1") {
        sessionStorage.removeItem("ow:pick-model");
        setDialog("+");
      }
    } catch { /* private window */ }
  }, []);

  if (!settings) return <div className="text-ui text-muted">{t("manage.loading")}</div>;

  const info = ps.info;
  const knownNames = ps.providers.map((p) => p.name);
  const dialogEl = dialog ? (
    <PickModelDialog
      providers={ps.providers}
      settings={settings}
      keylessOk={ps.keylessOk}
      configure={dialog === "+" ? null : dialog}
      onClose={() => setDialog("")}
      onChanged={() => {
        refreshSettings();
        ps.refreshProviders();
      }}
    />
  ) : null;

  if (ps.sel === null) {
    return (
      <div>
        <ProviderGroups ps={ps} tp="set" models={settings.models} />
        <ComposerPickerCard settings={settings} providers={ps.providers} onChanged={refreshSettings} onPick={() => setDialog("+")} onConfigure={(m) => setDialog(m)} />
        {dialogEl}
      </div>
    );
  }

  return (
    <div>
      <ProviderForm
        ps={ps}
        tp="set"
        footer={<ProviderKeyFooter info={info} credentialed={ps.credentialed} onRemove={ps.removeKey} />}
      />

      {info?.env_key && (
        <p className="text-meta text-muted mt-3 leading-relaxed" data-testid="set-env-key-note">
          {t(info.key_source === "env" ? "manage.env_key_help" : "manage.env_key_fallback_help", { var: info.env_key })}
        </p>
      )}

      {/* Keys wallet: once a machine is enrolled, a provider with a STORED key can
          deploy it (sealed; the machine owns its copy). Keyless providers (Ollama)
          have nothing in the wallet — no row. */}
      {ps.credentialed && ps.sel && <WalletChips profiles={[`provider:${ps.sel}`]} />}

      {info?.kind === "local" ? (
        // A local provider: the machine, then what the server holds (UX-055).
        <>
          <YourSystem />
          <LocalModelsTable
            provider={ps.sel}
            curated={settings.models}
            defaultModel={settings.model}
            onChanged={refreshSettings}
            onConfigure={(m) => setDialog(m)}
          />
          {dialogEl}
        </>
      ) : info?.configured ? (
        <div className="mt-6">
          <div className={SEC_H + " mb-1.5"}>{t("manage.models")}</div>
          <p className="text-meta text-muted mb-2.5 leading-relaxed">
            {t("manage.models_help")}
          </p>
          <ModelChecklist
            provider={ps.sel}
            knownProviders={knownNames}
            suggested={info?.suggested_models || []}
            curated={settings.models}
            defaultModel={settings.model}
            labels={settings.model_labels}
            onChanged={(next) => setSettings((s) => (s ? { ...s, models: next.models, model: next.model } : s))}
          />
        </div>
      ) : (
        // Unconfigured providers still show their curated models as a read-only preview — what a
        // key unlocks is part of deciding to get one at all (owner ask, 2026-07-04).
        (info?.suggested_models?.length || 0) > 0 && (
          <div className="mt-6" data-testid="model-preview">
            <div className={SEC_H + " mb-1.5"}>{t("manage.included_models")}</div>
            <p className="text-meta text-muted mb-2.5 leading-relaxed">
              {t(info?.auth === "oauth" ? "manage.included_models_help_signin" : "manage.included_models_help")}
            </p>
            <div className="space-y-1">
              {(info?.suggested_models || []).map((m) => {
                const full = ps.sel === "openai" ? m : `${ps.sel}:${m}`;
                return (
                  <div
                    key={m}
                    className="px-2.5 py-1.5 rounded-lg border border-line bg-paper text-ui text-muted"
                    title={full}
                  >
                    {settings.model_labels?.[full] || m}
                  </div>
                );
              })}
            </div>
          </div>
        )
      )}
    </div>
  );
}

// The gallery view's "In the composer's picker" card: every curated model across providers,
// with its provider tag. Unticking removes it from the picker; adding happens from a
// provider's card (the ModelChecklist there has the suggested list + free-type add).
function ComposerPickerCard({
  settings,
  providers,
  onChanged,
  onPick,
  onConfigure,
}: {
  settings: ModelSettings;
  providers: ProviderInfo[];
  onChanged: () => void;
  onPick: () => void;
  onConfigure: (model: string) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const names = providers.map((p) => p.name);
  const provOf = (id: string) => {
    const i = id.indexOf(":");
    return i > 0 && names.includes(id.slice(0, i)) ? id.slice(0, i) : "openai";
  };
  const tag = (id: string) => {
    const p = providers.find((x) => x.name === provOf(id));
    return (p?.title || provOf(id)).split(" (")[0];
  };
  // "ollama:qwen3-coder:30b" reads as "qwen3-coder:30b"; the provider sits beside it.
  const shortId = (id: string) => (id.startsWith(provOf(id) + ":") ? id.slice(provOf(id).length + 1) : id);
  // The card says the provider on its second line, so a label that ends in it
  // ("GPT-5.6 Sol · OpenAI") loses that tail on the first.
  const title = (id: string) => {
    const label = settings.model_labels?.[id] || shortId(id);
    const tail = ` · ${tag(id)}`;
    return label.endsWith(tail) ? label.slice(0, -tail.length) : label;
  };
  const detail = (id: string) => {
    const cfg = settings.model_config?.[id];
    const bits = [tag(id)];
    if (cfg?.context_size) bits.push(`${Math.round(cfg.context_size / 1024)}K`);
    if (cfg?.thinking) bits.push(t("manage.thinking_tag"));
    return bits.join(" · ");
  };
  const q = query.trim().toLowerCase();
  const shown = settings.models.filter((id) => !q || id.toLowerCase().includes(q) || (settings.model_labels?.[id] || "").toLowerCase().includes(q));
  return (
    <div className="mt-10" data-testid="composer-picker">
      <div className="flex items-center mb-2">
        <div className={SEC_H + " uppercase tracking-wide"}>{t("manage.composer_picker_title")}</div>
        <label className="ml-auto flex items-center gap-1.5 rounded-lg border border-line bg-panel px-2.5 py-1 w-[220px] text-meta">
          <Icon name="search" size={12} className="text-faint shrink-0" />
          <input className="flex-1 bg-transparent outline-none" placeholder={t("manage.picker_search")} value={query} onChange={(e) => setQuery(e.target.value)} data-testid="picker-search" />
        </label>
        <button
          className="ml-2 grid place-items-center w-[30px] h-[30px] rounded-lg border border-line bg-panel text-muted hover:text-ink hover:border-lineStrong transition-colors"
          title={t("manage.pick_model_btn")}
          onClick={onPick}
          data-testid="pick-model-btn"
        >
          <Icon name="plus" size={14} />
        </button>
      </div>
      {/* Each model is a card like the provider cards above: name, then provider and any
          saved settings; ✕ and "Make default" show on hover. No table, no dividers. */}
      <div className={"grid grid-cols-2 xl:grid-cols-3 gap-3" + (shown.length > 9 ? " max-h-[262px] overflow-y-auto pr-1 -mr-1" : "")}>
        {shown.map((id) => {
          const isDefault = id === settings.model;
          return (
            <div
              className="mlist-row group relative flex items-center gap-3 rounded-xl border border-line bg-panel/50 px-5 py-4 min-h-[70px] text-ui hover:border-lineStrong transition-colors"
              key={id}
              data-testid={`picker-row-${id}`}
            >
              <span className="min-w-0 flex-1">
                <button className="block w-full truncate text-left leading-tight hover:underline underline-offset-2" title={t("manage.configure_model")} onClick={() => onConfigure(id)}>
                  {title(id)}
                </button>
                <span className="flex items-center gap-2 mt-1 text-meta text-faint">
                  <span className="truncate">{detail(id)}</span>
                  {isDefault ? (
                    <span className="mlist-default">{t("models.default_badge")}</span>
                  ) : (
                    <button className="mlist-make" onClick={() => setDefaultModel(id).then(() => onChanged())}>
                      {t("models.make_default")}
                    </button>
                  )}
                </span>
              </span>
              {!isDefault && (
                <button
                  className="text-faint hover:text-ink opacity-0 group-hover:opacity-100 transition-opacity"
                  title={t("manage.remove_from_picker")}
                  onClick={() => removeModel(id).then((r) => r.ok && onChanged())}
                  data-testid={`picker-remove-${id}`}
                >
                  ✕
                </button>
              )}
            </div>
          );
        })}
        {shown.length === 0 && <div className="px-1 py-2 text-meta text-faint">—</div>}
      </div>
      <p className="text-meta text-muted mt-3 leading-relaxed">{t("manage.composer_picker_help_v3")}</p>
    </div>
  );
}

// -- Connectors ---------------------------------------------------------------
// The Connectors tab body moved to connectors/ConnectorsSection.tsx (UX-DECISIONS
// §21: connected-first list + per-connector detail subpages). This file keeps the
// shared building blocks the detail pages reuse: ConnectSetup, ConnectorTools, and
// the two-way blocks (Allowlist/Unauthorized/ListeningSessions).

// Parked messages from senders not on the allow-list (§19). The gateway keeps what they said
// instead of dropping it, so first contact is one step: Allow & deliver replays the original
// message through the normal inbound path — no "message the bot again".
// With `teamId` (the Slack-workspaces page) only that workspace's parked messages show;
// resolving routes the allow to the right workspace server-side (the item carries its team).
export function UnauthorizedBlock({
  c,
  onChanged,
  teamId,
}: {
  c: Connector;
  onChanged: () => void;
  teamId?: string;
}) {
  const { t } = useTranslation();
  const items = (c.unauthorized ?? []).filter(
    (m) => teamId === undefined || m.team_id === teamId,
  );
  if (items.length === 0) return null;
  const act = async (id: string, action: "dismiss" | "allow" | "allow_deliver") => {
    await resolveUnauthorized(c.name, id, action);
    onChanged();
  };
  return (
    <div
      className="border-t border-line px-3.5 py-3"
      data-testid={teamId ? `unauthorized-${c.name}-${teamId}` : `unauthorized-${c.name}`}
    >
      <div className={SEC_H + " mb-2"}>
        {t("manage.parked_title", { n: items.length })}
      </div>
      <div className="space-y-2">
        {items.map((m) => (
          <div key={m.id} className="rounded-xl border border-line bg-paper p-2.5">
            <div className="flex items-center gap-2 text-meta text-muted">
              <span className="font-medium text-ink">{m.user_name || m.user_id}</span>
              <span>{t("manage.parked_in", { chat: m.chat_name || m.chat_id })}</span>
              <span className="ml-auto shrink-0">{relTime(m.ts) || ""}</span>
            </div>
            <div className="text-ui mt-1 break-words">{m.text}</div>
            <div className="flex items-center gap-1.5 mt-2">
              <button
                className="text-meta px-2 py-1 rounded-md bg-accent text-white"
                data-testid={`parked-allow-deliver-${m.id}`}
                title={t("manage.parked_allow_deliver_tip")}
                onClick={() => act(m.id, "allow_deliver")}
              >
                {t("manage.parked_allow_deliver")}
              </button>
              <button
                className={BTN_BORDERED}
                data-testid={`parked-allow-${m.id}`}
                title={t("manage.parked_allow_tip")}
                onClick={() => act(m.id, "allow")}
              >
                {t("manage.parked_allow_only")}
              </button>
              <button
                className="text-meta px-2 py-1 rounded-md text-faint hover:text-danger"
                data-testid={`parked-dismiss-${m.id}`}
                title={t("manage.parked_dismiss_tip")}
                onClick={() => act(m.id, "dismiss")}
              >
                {t("manage.parked_dismiss")}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Which sessions listen to this connector's channels — the per-connector cut of the global
// Channel-subscriptions table (Integrations ▸ Messaging routing). Subscribing happens from a
// session's Sources ▸ Channels panel; here the owner can see and revoke.
export function ListeningSessionsBlock({ c }: { c: Connector }) {
  const { t } = useTranslation();
  const [subs, setSubs] = useState<Subscription[] | null>(null);
  const load = () => getSubscriptions().then(setSubs).catch(() => setSubs([]));
  useEffect(() => {
    load();
  }, [c.name]);
  const platformOf = (channel: string) =>
    channel.includes(":") ? channel.split(":")[0] : "slack";
  const mine = (subs ?? []).filter((s) => platformOf(s.channel) === c.name);
  return (
    <div className="border-t border-line px-3.5 py-3" data-testid={`listening-${c.name}`}>
      <div className={SEC_H + " mb-2"}>{t("manage.listening_title", { title: c.title, n: mine.length })}</div>
      {mine.length === 0 ? (
        <div className="text-meta text-faint">
          {t("manage.listening_empty")}
        </div>
      ) : (
        <div className="space-y-1.5">
          {mine.map((s) => (
            <div className="flex items-center gap-2 text-ui" key={s.session_id + s.channel}>
              <span className="min-w-0 truncate" title={s.session_id}>
                {s.session_title || s.session_id}
                {s.agent ? <span className="text-faint"> · {s.agent}</span> : null}
              </span>
              <span className="text-muted shrink-0" title={s.channel}>
                ← {s.channel_name ? `#${s.channel_name}` : s.channel}
              </span>
              <button
                className="ml-auto text-faint hover:text-danger shrink-0"
                title={t("manage.listening_unsub_tip")}
                onClick={async () => {
                  await unsubscribeChannel(s.session_id, s.channel);
                  load();
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Who may message this two-way bot. Recent senders surface here once they DM/mention the bot, so you
// can Allow them; allowed users are chips you can remove. (Was orphaned in the super-agent view.)
// With `teamId` (the Slack-workspaces page) the list is that WORKSPACE's — ids are
// workspace-scoped, so allow/remove target `slack:team:<id>` and recents filter to the team.
export function AllowlistBlock({
  c,
  onChanged,
  teamId,
  allowed,
  allowedNames,
}: {
  c: Connector;
  onChanged: () => void;
  teamId?: string;
  allowed?: string[];
  allowedNames?: Record<string, string | null>;
}) {
  const { t } = useTranslation();
  const allowedUsers = allowed ?? c.allowed_users;
  const names = allowedNames ?? c.allowed_user_names;
  const recent = (c.recent ?? []).filter(
    (r) => teamId === undefined || r.team_id === teamId,
  );
  const unknownRecent = recent.filter((r) => !r.authorized);

  return (
    <div className="border-t border-line px-3.5 py-3 grid grid-cols-2 gap-5">
      <div>
        <div className={SEC_H + " mb-2"}>{t("manage.allowed_to_message")}</div>
        <div className="flex flex-wrap gap-1.5">
          {allowedUsers.length === 0 && (
            <span className="text-meta text-faint">{t("manage.allowed_empty")}</span>
          )}
          {allowedUsers.map((u) => (
            <span
              key={u}
              className="inline-flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-full bg-paper border border-line text-meta"
              title={t("manage.id_title", { id: u })}
            >
              <span className="w-4 h-4 rounded-full bg-accentSoft text-accent grid place-items-center text-[9px] font-bold">
                {initials(names?.[u] || u)}
              </span>
              {names?.[u] || u}
              <button
                className="w-4 h-4 grid place-items-center text-faint hover:text-danger"
                title={t("common.remove")}
                onClick={async () => {
                  await disallowUser(c.name, u, teamId);
                  onChanged();
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      </div>
      <div>
        <div className={SEC_H + " mb-2"}>{t("manage.recent_senders")}</div>
        {unknownRecent.length === 0 ? (
          <div className="text-meta text-faint">{t("manage.recent_empty")}</div>
        ) : (
          <div className="space-y-1.5">
            {unknownRecent.map((r) => (
              <div className="flex items-center gap-2 text-ui" key={r.user_id}>
                <span className="w-5 h-5 rounded-full bg-paper border border-line grid place-items-center text-[9px] font-bold text-muted shrink-0">
                  {initials(r.user_name || "?")}
                </span>
                <span className="min-w-0 truncate" title={t("manage.id_title", { id: r.user_id })}>
                  {r.user_name || t("manage.unknown")} <span className="text-faint">· {r.chat_type}</span>
                </span>
                <button
                  className="ml-auto text-meta px-2 py-0.5 rounded-md bg-accent text-white shrink-0"
                  onClick={async () => {
                    await allowUser(c.name, r.user_id, teamId);
                    onChanged();
                  }}
                >
                  {t("approval.allow")}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Who may APPROVE from this Telegram bot — a stricter list than "allowed to message"
// (OPE-217 / #712). Empty means: the one person in a bound private chat may approve;
// a bound group needs owners chosen here before anyone can.
export function TelegramApprovalOwnersBlock({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const { t } = useTranslation();
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState("");
  const owners = c.approval_owner_ids ?? [];
  const names = { ...(c.allowed_user_names ?? {}), ...(c.approval_owner_names ?? {}) };
  const candidates = c.allowed_users.filter((u) => !owners.includes(u));
  const label = (u: string) => names[u] || u;
  const add = async (userId: string) => {
    const result = await addTelegramApprovalOwner(userId, names[userId] || undefined);
    if (!result.ok) {
      setErr(result.error || t("manage.owners_update_failed"));
      return;
    }
    setErr(null);
    setPick("");
    onChanged();
  };
  const remove = async (userId: string) => {
    const result = await removeTelegramApprovalOwner(userId);
    if (!result.ok) {
      setErr(result.error || t("manage.owners_update_failed"));
      return;
    }
    setErr(null);
    onChanged();
  };
  return (
    <div className="border-t border-line px-3.5 py-3" data-testid="telegram-approval-owners">
      <div className={SEC_H + " mb-1"}>{t("manage.approval_owners")}</div>
      <div className="text-meta text-faint mb-2">{t("manage.approval_owners_help")}</div>
      <div className="flex flex-wrap items-center gap-1.5">
        {owners.length === 0 && (
          <span className="text-meta text-faint">{t("manage.approval_owners_empty")}</span>
        )}
        {owners.map((u) => (
          <span
            key={u}
            className="inline-flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-full bg-paper border border-line text-meta"
            title={t("manage.id_title", { id: u })}
            data-testid={`telegram-approval-owner-${u}`}
          >
            <span className="w-4 h-4 rounded-full bg-accentSoft text-accent grid place-items-center text-[9px] font-bold">
              {initials(label(u))}
            </span>
            {label(u)}
            <button
              className="w-4 h-4 grid place-items-center text-faint hover:text-danger"
              title={t("common.remove")}
              onClick={() => remove(u)}
            >
              ×
            </button>
          </span>
        ))}
        {candidates.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <select
              className="text-meta bg-paper border border-line rounded-md px-1.5 py-0.5"
              value={pick}
              onChange={(e) => setPick(e.target.value)}
              data-testid="telegram-approval-owner-pick"
            >
              <option value="">{t("manage.approval_owners_pick")}</option>
              {candidates.map((u) => (
                <option key={u} value={u}>
                  {label(u)}
                </option>
              ))}
            </select>
            <button
              className="text-meta px-2 py-0.5 rounded-md bg-accent text-white disabled:opacity-50"
              disabled={!pick}
              onClick={() => pick && add(pick)}
              data-testid="telegram-approval-owner-add"
            >
              {t("manage.add_btn")}
            </button>
          </span>
        )}
        {err && <span className="basis-full text-meta text-warnInk">{err}</span>}
      </div>
    </div>
  );
}

export function ConnectorTools({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const { t } = useTranslation();
  const toggle = async (toolName: string, enabled: boolean) => {
    await updateConnectorTools(c.name, { [toolName]: enabled });
    onChanged();
  };
  if (!c.tools?.length)
    return (
      <div className="border-t border-line px-3.5 py-3 text-ui text-muted">
        {t("manage.connector_no_tools")}
      </div>
    );
  return (
    <div className="border-t border-line px-3.5 py-3">
      <div className={SEC_H + " mb-2"}>{t("manage.tools_exposed")}</div>
      <div className="space-y-1.5">
        {c.tools.map((tool) => (
          <label
            className="flex items-start gap-2.5 p-2 rounded-lg border border-line bg-paper"
            key={tool.name}
          >
            <input
              type="checkbox"
              className="mt-0.5 shrink-0"
              checked={tool.enabled}
              onChange={(e) => toggle(tool.name, e.target.checked)}
            />
            <span className="min-w-0">
              <span className="block text-ui">{tool.label}</span>
              <span className="block text-meta text-faint">
                {t("manage.tool_asks_approval", { name: tool.name, kind: tool.kind })}
              </span>
              <span className="block text-meta text-faint">{tool.description}</span>
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

// Exported: also hosted inside the SourcesDrawer's connect-in-context child panel, so a
// recommended connector can be connected without leaving the session (owner ask, 2026-07-03).
export function ConnectSetup({
  c,
  cloud,
  onConnected,
  manualOnly = false,
}: {
  c: Connector;
  cloud: CloudStatus | null;
  onConnected: () => void;
  // The add-modal's Manual pane: the one-click button lives on the sibling
  // pill, so don't render the managed block again here.
  manualOnly?: boolean;
}) {
  const { t } = useTranslation();
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false); // managed flow: browser is open
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    const res = await connectConnector(c.name, values);
    setBusy(false);
    if (res.ok) onConnected();
    else setError(res.error || t("manage.could_not_connect"));
  };

  const oneClick = async () => {
    setError(null);
    const res = await connectManaged(c.name);
    // Completion arrives via the tab's poll: the broker form-POSTs the profile
    // to the sidecar, the connector flips to connected, this card closes itself.
    if (res.ok) setWaiting(true);
    else setError(res.error || t("manage.could_not_start_managed"));
  };

  const mcpOneClick = async () => {
    setError(null);
    const res = await connectMcpBacked(c.name);
    // Completion likewise arrives via the poll — the sidecar flips the connector
    // to connected once the local OAuth flow lands.
    if (res.ok) setWaiting(true);
    else setError(res.error || t("manage.could_not_start_connect"));
  };

  return (
    <div className="border-t border-line px-3.5 py-3 space-y-3">
      {c.mcp && !manualOnly && (
        /* MCP-backed one-click needs no cloud sign-in — the OAuth flow is local. */
        <div className="space-y-2" data-testid="mcp-connect">
          <button className={BTN_ACCENT} onClick={mcpOneClick} disabled={waiting}>
            {waiting ? t("manage.check_browser") : t("manage.connect_one_click", { title: c.title })}
          </button>
          {c.fields.length > 0 && (
            <div className="text-meta text-faint">{t("manage.or_connect_manually")}</div>
          )}
        </div>
      )}
      {c.managed && !c.mcp && !manualOnly && (
        <div className="space-y-2" data-testid="managed-connect">
          {c.managed_paused ? (
            // One-click temporarily off (e.g. Google pending CASA verification):
            // a visibly-parked button, and the manual path below stays fully live.
            <>
              <button className={BTN_ACCENT + " opacity-50"} disabled data-testid="managed-coming-soon">
                {t("manage.connect_one_click", { title: c.title })}
                <span className="ml-2 text-label font-medium px-1.5 py-0.5 rounded-full bg-white/25">
                  {t("manage.coming_soon")}
                </span>
              </button>
              <div className="text-meta text-faint">
                {t("manage.one_click_coming")}
              </div>
            </>
          ) : cloud?.signed_in ? (
            <button className={BTN_ACCENT} onClick={oneClick} disabled={waiting}>
              {waiting ? t("manage.check_browser") : t("manage.connect_one_click", { title: c.title })}
            </button>
          ) : cloud ? (
            <CloudSignInInline
              blurb={t("manage.signin_unlocks", { title: c.title })}
            />
          ) : (
            // Status unknown (fetch pending/failed): never show the sign-in ask to a
            // possibly-signed-in user (FB-013); the host keeps polling.
            <CloudStatusPending />
          )}
          {!c.managed_paused && cloud?.signed_in && (
            <div className="text-meta text-faint">{t("manage.or_connect_manually")}</div>
          )}
        </div>
      )}
      {c.instructions.length > 0 && (
        <ol className="list-decimal pl-4 text-ui text-muted leading-relaxed space-y-1">
          {c.instructions.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
      {c.fields.map((f) => (
        <label className="conn-field" key={f.key}>
          <span className="conn-field-label">
            {f.label}
            {!f.required && <em> ({t("manage.optional")})</em>}
          </span>
          <input
            type={f.secret ? "password" : "text"}
            placeholder={f.placeholder}
            value={values[f.key] || ""}
            spellCheck={false}
            onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
          />
          {f.help && <span className="conn-field-help">{f.help}</span>}
        </label>
      ))}
      <div>
        <button className={BTN_ACCENT} onClick={submit} disabled={busy}>
          {busy ? t("manage.validating") : t("manage.connect")}
        </button>
      </div>
      {error && <div className="text-ui text-danger">{error}</div>}
    </div>
  );
}
