// PersonaView — the persona detail page (UX-035 redesign). Identity header + Enable toggle;
// About as markdown with a screenshot carousel (bundle media/); ONE Connectors section with
// Status | Enable columns (replacing "Connections for full benefit" + "New sessions get by
// default" — they were the same list rendered twice); Tool calls as a collapsed disclosure
// under Advanced; a defaults footer; and the management group that moved off the list page
// (in picker, make default, export, delete).
//
// Data: GET /v1/personas/{id} on mount; /v1/connectors threads real brand colors into the
// badges via visualFor(). Media loads through authenticated fetch → object URLs (a plain
// <img src> can't carry the sidecar token).

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deletePersona,
  exportPersona,
  getConnectors,
  getPersonaDetail,
  getPersonaMediaUrl,
  setPersonaConnection,
  setPersonaEnabled,
  updatePersona,
  type PersonaDetail,
} from "../api";
import { chooseFolder } from "../tauri";
import { ConnectorBadge } from "../connectors/ConnectorIcon";
import { fullPersonaName } from "../personaScope";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { Toggle } from "./Toggle";
import { indexConnectors, labelFor, visualFor, type ConnectorMap } from "../connectors/visuals";

const SEC_H = "text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";
const TAG_CORE =
  "text-[11px] px-1.5 py-0.5 rounded-full bg-warnSoft/70 text-warnInk border border-warnInk/15";
const TAG_MCP = "text-[11px] px-1.5 py-0.5 rounded border border-line text-faint";
const BTN_ACCENT = "text-[12px] px-2.5 py-1.5 rounded-lg bg-accent text-white shrink-0";
const BTN_BORDERED =
  "text-[12px] px-2.5 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0 disabled:opacity-40";
const GRP = "rounded-xl2 border border-line bg-panel divide-y divide-line overflow-hidden";
const COL_STATUS = "w-[96px] flex justify-end items-center shrink-0";
const COL_ENABLE = "w-[64px] flex justify-center items-center shrink-0";

export function PersonaView({
  personaId,
  onBack,
  onOpenIntegrations,
}: {
  personaId: string;
  onBack?: () => void;
  onOpenIntegrations?: () => void;
}) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<PersonaDetail | null>(null);
  const [byName, setByName] = useState<ConnectorMap>({});
  const [error, setError] = useState<string | null>(null);
  const [mediaUrls, setMediaUrls] = useState<string[]>([]);
  const [shot, setShot] = useState(0);
  const [showTools, setShowTools] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    let urls: string[] = [];
    setDetail(null);
    setError(null);
    setMediaUrls([]);
    setShot(0);
    getPersonaDetail(personaId)
      .then(async (d) => {
        if (!live) return;
        setDetail(d);
        const loaded = await Promise.all(
          (d.media || []).map((name) => getPersonaMediaUrl(personaId, name).catch(() => null)),
        );
        urls = loaded.filter(Boolean) as string[];
        if (live) setMediaUrls(urls);
      })
      .catch(() => live && setError(t("persona.load_error")));
    getConnectors()
      .then((list) => live && setByName(indexConnectors(list)))
      .catch(() => {});
    return () => {
      live = false;
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [personaId]);

  const toggleEnabled = async (next: boolean) => {
    setDetail((d) => (d ? { ...d, enabled: next } : d)); // optimistic
    const r = await setPersonaEnabled(personaId, next);
    if (!r.ok) getPersonaDetail(personaId).then(setDetail).catch(() => {});
  };

  const toggleDefault = async (connector: string, next: boolean) => {
    const r = await setPersonaConnection(personaId, connector, next);
    if (r.default_connections) {
      setDetail((d) => (d ? { ...d, default_connections: r.default_connections! } : d));
    } else {
      getPersonaDetail(personaId).then(setDetail).catch(() => {});
    }
  };

  const patch = async (body: { surfaced?: boolean; default?: boolean }) => {
    await updatePersona(personaId, body);
    getPersonaDetail(personaId).then(setDetail).catch(() => {});
  };

  const exportBundle = async () => {
    const dir = await chooseFolder();
    if (!dir) return;
    const r = await exportPersona(personaId, dir);
    setMsg(r.ok ? t("persona.exported_to", { path: r.path }) : r.error || t("persona.export_failed"));
  };

  const header = (
    <div className="h-12 shrink-0 px-5 flex items-center gap-3 border-b border-line bg-paper">
      {onBack && (
        <>
          <button
            className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink"
            onClick={onBack}
          >
            <Icon name="arrowLeft" size={15} /> {t("persona.back")}
          </button>
          <span className="text-faint">·</span>
        </>
      )}
      <span className="text-[13px] font-semibold">{t("persona.persona")}</span>
    </div>
  );

  if (error || !detail) {
    return (
      <main className="flex-1 min-w-0 flex flex-col bg-paper">
        {header}
        <div className="p-12 text-center text-faint text-[13px]">{error || t("persona.loading")}</div>
      </main>
    );
  }

  // One Connectors table: manifest recommends ∪ the persona-default rows. A ref present in
  // both renders once — status from the connect state, toggle from the default state.
  const defaultsByRef = new Map(detail.default_connections.map((c) => [c.connector, c]));
  const rows: {
    key: string;
    kind: string;
    ref: string;
    reason: string;
    tier: string;
    connected: boolean;
    dflt?: { enabled: boolean; connected: boolean };
  }[] = detail.recommends.map((r) => ({
    key: `${r.kind}:${r.ref}`,
    kind: r.kind,
    ref: r.ref,
    reason: r.reason,
    tier: r.tier,
    connected: r.connected,
    dflt: r.kind === "connector" ? defaultsByRef.get(r.ref) : undefined,
  }));
  for (const c of detail.default_connections) {
    if (!rows.some((r) => r.kind === "connector" && r.ref === c.connector)) {
      rows.push({
        key: `connector:${c.connector}`,
        kind: "connector",
        ref: c.connector,
        reason: "",
        tier: "optional",
        connected: c.connected,
        dflt: c,
      });
    }
  }

  return (
    <main className="flex-1 min-w-0 flex flex-col bg-paper">
      {header}
      <div className="flex-1 overflow-y-auto hairline-scroll">
        <div className="max-w-3xl mx-auto px-7 py-6 space-y-6">
          {/* identity + enable (no coworker glyph — owner 2026-08-21) */}
          <header className="flex items-start gap-3.5">
            <div className="min-w-0">
              <h1 className="text-[20px] font-semibold tracking-tight">
                {fullPersonaName(detail.name, personaId)}
              </h1>
              <p className="text-[13px] text-muted mt-0.5">{detail.tagline}</p>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="text-[12px] text-muted">{detail.enabled ? t("persona.enabled") : t("persona.disabled")}</span>
              <Toggle checked={detail.enabled} onChange={toggleEnabled} title={t("persona.enable_title")} />
            </div>
          </header>

          {/* about: bundle markdown + screenshot carousel */}
          {(detail.description || mediaUrls.length > 0) && (
            <section>
              <div className={`${SEC_H} mb-1.5`}>{t("persona.about")}</div>
              {detail.description && (
                <div className="text-[14px] leading-relaxed text-ink/90">
                  <Markdown text={detail.description} />
                </div>
              )}
              {mediaUrls.length > 0 && (
                <div className="mt-3.5">
                  <div className="flex items-center gap-2">
                    {mediaUrls.length > 1 && (
                      <button
                        className="w-7 h-7 rounded-full border border-line bg-panel text-muted hover:text-ink hover:border-lineStrong shrink-0"
                        aria-label={t("persona.prev_screenshot")}
                        onClick={() => setShot((s) => (s - 1 + mediaUrls.length) % mediaUrls.length)}
                      >
                        ‹
                      </button>
                    )}
                    <img
                      src={mediaUrls[shot]}
                      alt={t("persona.screenshot_alt", { name: detail.name, n: shot + 1 })}
                      className="flex-1 min-w-0 rounded-xl border border-line bg-panel"
                      data-testid="persona-media"
                    />
                    {mediaUrls.length > 1 && (
                      <button
                        className="w-7 h-7 rounded-full border border-line bg-panel text-muted hover:text-ink hover:border-lineStrong shrink-0"
                        aria-label={t("persona.next_screenshot")}
                        onClick={() => setShot((s) => (s + 1) % mediaUrls.length)}
                      >
                        ›
                      </button>
                    )}
                  </div>
                  {mediaUrls.length > 1 && (
                    <div className="flex justify-center gap-1.5 mt-2">
                      {mediaUrls.map((_, i) => (
                        <button
                          key={i}
                          aria-label={t("persona.screenshot_n", { n: i + 1 })}
                          className={
                            "w-1.5 h-1.5 rounded-full " + (i === shot ? "bg-accent" : "bg-lineStrong")
                          }
                          onClick={() => setShot(i)}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </section>
          )}

          {/* connectors — one table, Status | Enable columns */}
          {rows.length > 0 && (
            <section>
              <div className={`${SEC_H} mb-1.5 flex items-baseline`}>
                <span>{t("persona.connectors")}</span>
                <span className="ml-auto flex font-semibold text-[11px] text-faint normal-case tracking-normal">
                  <span className={COL_STATUS}>{t("persona.col_status")}</span>
                  <span className={COL_ENABLE}>{t("persona.col_enable")}</span>
                </span>
              </div>
              <div className={GRP}>
                {rows.map((r) => (
                  <div className="flex items-center gap-3 px-4 py-3" key={r.key}>
                    <ConnectorBadge connector={visualFor(r.ref, r.kind, byName)} size={32} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] font-medium">{labelFor(r.ref, byName)}</span>
                        {r.kind === "mcp" ? (
                          <span className={TAG_MCP}>MCP</span>
                        ) : r.tier === "core" ? (
                          <span className={TAG_CORE}>{t("persona.core_tag")}</span>
                        ) : null}
                      </div>
                      {r.reason && <div className="text-[12px] text-muted">{r.reason}</div>}
                    </div>
                    <span className={COL_STATUS}>
                      {r.connected ? (
                        <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-okSoft text-ok border border-okLine">
                          {t("persona.connected")}
                        </span>
                      ) : (
                        <button
                          className={r.tier === "core" && r.kind !== "mcp" ? BTN_ACCENT : BTN_BORDERED}
                          onClick={onOpenIntegrations}
                        >
                          {r.kind === "mcp" ? t("persona.add") : t("persona.connect")}
                        </button>
                      )}
                    </span>
                    <span className={COL_ENABLE}>
                      {r.dflt ? (
                        <Toggle
                          checked={r.dflt.enabled}
                          disabled={!r.connected}
                          onChange={(next) => toggleDefault(r.ref, next)}
                          title={
                            r.connected
                              ? t("persona.on_by_default")
                              : t("persona.connect_this_first")
                          }
                        />
                      ) : (
                        <span className="text-faint text-[11px]">—</span>
                      )}
                    </span>
                  </div>
                ))}
              </div>
              <p className="text-[12px] text-faint mt-1.5 px-1">
                {t("persona.defaults_footnote")}
              </p>
            </section>
          )}

          {/* advanced: tool calls, collapsed by default (everyday users don't need these) */}
          {detail.tools.length > 0 && (
            <section>
              <div className={`${SEC_H} mb-1.5`}>{t("persona.advanced")}</div>
              <div className="rounded-xl2 border border-line bg-panel">
                <button
                  className="w-full flex items-center gap-2 px-4 py-2.5 text-left"
                  data-testid="tool-calls-disclosure"
                  onClick={() => setShowTools((v) => !v)}
                >
                  <Icon
                    name="chevronRight"
                    size={12}
                    className={"text-faint transition-transform" + (showTools ? " rotate-90" : "")}
                  />
                  <span className="text-[13px]">{t("persona.tool_calls")}</span>
                  <span className="ml-auto text-[12px] text-faint">{detail.tools.length}</span>
                </button>
                {showTools && (
                  <div className="px-4 pb-3 font-mono text-[12px] text-muted">
                    {detail.tools.join(" · ")}
                  </div>
                )}
              </div>
            </section>
          )}

          {/* defaults footer */}
          <section className="flex flex-wrap gap-x-8 gap-y-2 text-[13px]">
            {detail.recommended_models.length > 0 && (
              <div>
                <span className="text-faint">{t("persona.models_label")}</span> ·{" "}
                {detail.recommended_models.map((m, i) => (
                  <span key={m}>
                    <span className="font-mono">{m}</span>
                    {i < detail.recommended_models.length - 1 ? ", " : ""}
                  </span>
                ))}
              </div>
            )}
            {detail.default_permission_mode && (
              <div>
                <span className="text-faint">{t("persona.default_mode_label")}</span> · {detail.default_permission_mode}
              </div>
            )}
            <div>
              <span className="text-faint">{t("persona.workspace_label")}</span> ·{" "}
              {detail.requires_folder ? t("persona.workspace_picked") : t("persona.workspace_scratch")}
            </div>
          </section>

          {/* management — the controls that left the list page (UX-035) */}
          <section className="border-t border-line pt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px]">
            <label className="flex items-center gap-2 text-muted select-none">
              <input
                type="checkbox"
                checked={detail.surfaced}
                disabled={!detail.enabled}
                data-testid="persona-surfaced"
                onChange={(e) => patch({ surfaced: e.target.checked })}
              />
              {t("persona.show_in_picker")}
            </label>
            <button
              className={BTN_BORDERED}
              disabled={detail.default || !detail.enabled}
              data-testid="persona-make-default"
              onClick={() => patch({ default: true })}
            >
              {detail.default ? t("persona.default_for_new") : t("persona.make_default")}
            </button>
            {!detail.builtin && (
              <button className={BTN_BORDERED} data-testid="persona-export" onClick={exportBundle}>
                {t("persona.export")}
              </button>
            )}
            {!detail.builtin &&
              (confirmDel ? (
                <span className="flex items-center gap-1.5">
                  <button
                    className="text-[12px] px-2.5 py-1.5 rounded-lg bg-danger text-white"
                    data-testid="persona-delete-confirm"
                    onClick={async () => {
                      const r = await deletePersona(personaId);
                      if (r.ok) onBack?.();
                      else setMsg(r.error || t("persona.delete_failed"));
                    }}
                  >
                    {t("persona.delete")}
                  </button>
                  <button className={BTN_BORDERED} onClick={() => setConfirmDel(false)}>
                    {t("persona.keep")}
                  </button>
                </span>
              ) : (
                <button
                  className="text-[13px] text-danger/80 hover:text-danger"
                  data-testid="persona-delete"
                  onClick={() => setConfirmDel(true)}
                >
                  {t("persona.delete_ellipsis")}
                </button>
              ))}
            {msg && <span className="text-muted">{msg}</span>}
          </section>
        </div>
      </div>
    </main>
  );
}
