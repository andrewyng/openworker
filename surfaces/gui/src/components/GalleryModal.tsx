import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  cloudLogin,
  getCloudGallery,
  getCloudGalleryDetail,
  getCloudStatus,
  getPersonas,
  installPersona,
  type CloudStatus,
  type GalleryDetail,
  type GalleryPersona,
} from "../api";
import { BrandIcon } from "./brandIcons";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { PersonaHero } from "./PersonaHero";

// The Persona Gallery, as a screen-sized modal over Settings ▸ Personas (the catalog
// wants room the inline section never had; installs finish back on the Personas page,
// which is why this is a modal and not a route). Three zones: header (search + source
// chips), a featured carousel (publisher-flagged), and the catalog list; every card
// opens the in-modal detail page — install only happens there, informed.
//
// Trust model unchanged: browsing requires the (free) cloud sign-in; the pitch is
// publisher metadata but the capabilities card is derived locally from the manifest
// by our own parser; installs land disabled pending consent under Personas.

const CARD = "rounded-xl border border-line bg-panel/60";
const BTN_ACCENT =
  "text-[13px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const CHIP = "text-[11px] px-1.5 py-0.5 rounded border border-line text-muted";

type Source = "all" | "openworker" | "team";

function sourceOf(p: GalleryPersona): Exclude<Source, "all"> {
  return p.publisher === "OpenWorker" ? "openworker" : "team";
}

function ConnectorChip({ name }: { name: string }) {
  return (
    <span className={CHIP + " inline-flex items-center gap-1"}>
      <BrandIcon name={name} size={12} />
      {name}
    </span>
  );
}

export function GalleryModal({
  onClose,
  onInstalled,
}: {
  onClose: () => void;
  onInstalled?: () => void;
}) {
  const { t } = useTranslation();
  const [cloud, setCloud] = useState<CloudStatus | null>(null);
  const [cards, setCards] = useState<GalleryPersona[]>([]);
  const [installed, setInstalled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<Source>("all");
  const [detailSlug, setDetailSlug] = useState<string | null>(null);
  const [detail, setDetail] = useState<GalleryDetail | null>(null);
  const [justInstalled, setJustInstalled] = useState(false);

  const reload = async () => {
    setLoading(true);
    const status = getCloudStatus().then(setCloud).catch(() => setCloud(null));
    getPersonas()
      .then((ps) => setInstalled(new Set(ps.map((p) => p.id))))
      .catch(() => {});
    try {
      const g = await getCloudGallery();
      setCards(g.ok ? g.personas : []);
      setUnavailable(!g.ok);
    } catch {
      setCards([]);
      setUnavailable(true);
    }
    // The signed-in check gates which body renders — wait for it too, so the
    // skeleton never flashes into the wrong state.
    await status;
    setLoading(false);
  };
  useEffect(() => {
    reload();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const signIn = async () => {
    setSigningIn(true);
    await cloudLogin(); // sidecar opens the browser; poll for completion
    setTimeout(() => {
      setSigningIn(false);
      reload();
    }, 3000);
  };

  const openDetail = async (slug: string) => {
    setDetailSlug(slug);
    setDetail(null);
    setMsg(null);
    setJustInstalled(false);
    const d = await getCloudGalleryDetail(slug).catch(() => null);
    setDetail(d ?? { ok: false, error: t("gallery.could_not_load") });
  };

  const install = async (slug: string) => {
    setBusy(true);
    setMsg(null);
    const r = await installPersona({ gallery_slug: slug });
    setBusy(false);
    if (!r.ok) {
      setMsg(r.error || t("gallery.install_failed"));
      return;
    }
    setInstalled((s) => new Set(s).add(slug));
    setJustInstalled(true);
    onInstalled?.(); // re-mounts the Personas list so the new persona shows in place
  };

  const q = query.trim().toLowerCase();
  const visible = cards.filter(
    (p) =>
      (source === "all" || sourceOf(p) === source) &&
      (!q || `${p.name} ${p.tagline} ${p.description}`.toLowerCase().includes(q)),
  );
  const featured = visible.filter((p) => p.featured);
  const teamCount = cards.filter((p) => sourceOf(p) === "team").length;

  const catalog = (
    <div data-testid="gallery-cards">
      <div className="flex items-center gap-2 mb-4">
        {(
          [
            ["all", t("gallery.filter_all")],
            ["openworker", t("gallery.filter_brand")],
            ["team", t("gallery.filter_team")],
          ] as [Source, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            className={
              "text-[12px] px-2.5 py-1 rounded-full border " +
              (source === key
                ? "border-accent text-accent bg-accentSoft"
                : "border-line text-muted hover:border-lineStrong")
            }
            onClick={() => setSource(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {unavailable && cloud?.signed_in && (
        <div className="text-[13px] text-muted">
          {t("gallery.unreachable")}
        </div>
      )}

      {featured.length > 0 && (
        <>
          <div className="text-[11px] uppercase tracking-[0.05em] text-faint font-semibold mb-2">
            {t("gallery.featured")}
          </div>
          <div className="flex gap-3 overflow-x-auto hairline-scroll pb-2 mb-5" data-testid="gallery-featured">
            {featured.map((p) => (
              <div
                key={p.slug}
                className="w-[240px] shrink-0 rounded-xl border border-line bg-panel/60 overflow-hidden cursor-pointer hover:border-lineStrong"
                onClick={() => openDetail(p.slug)}
              >
                <PersonaHero slug={p.slug} height={88} />
                <div className="p-3">
                  <div className="text-[13px] font-semibold">{p.name}</div>
                  <div className="text-[12px] text-muted leading-snug mt-0.5 mb-2">{p.tagline}</div>
                  <div className="flex flex-wrap gap-1.5">
                    {p.recommended_connectors.slice(0, 3).map((c) => (
                      <ConnectorChip key={c} name={c} />
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="text-[11px] uppercase tracking-[0.05em] text-faint font-semibold mb-2">
        {t("gallery.all_personas")}
      </div>
      <div className="space-y-2">
        {visible.map((p) => {
          const isInstalled = installed.has(p.slug);
          return (
            <div
              className={CARD + " p-3.5 flex items-center gap-4 cursor-pointer hover:border-lineStrong"}
              key={p.slug}
              data-testid={`gallery-${p.slug}`}
              onClick={() => openDetail(p.slug)}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="font-semibold text-[13px]">{p.name}</span>
                  <span className={CHIP}>{p.family}</span>
                  <span className="text-[11px] text-faint">
                    {t("gallery.version_publisher", { version: p.version, publisher: p.publisher })}
                  </span>
                </div>
                <div className="text-[13px] text-muted mb-1.5">{p.tagline}</div>
                {p.recommended_connectors.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {p.recommended_connectors.map((c) => (
                      <ConnectorChip key={c} name={c} />
                    ))}
                  </div>
                )}
              </div>
              <div className="shrink-0 flex items-center">
                {isInstalled ? (
                  <span className="text-[12px] text-muted">{t("gallery.installed")}</span>
                ) : (
                  <span className="text-[13px] text-accent">{t("gallery.view_install")}</span>
                )}
              </div>
            </div>
          );
        })}
        {visible.length === 0 && !unavailable && (
          <div className="text-[13px] text-muted py-4">
            {source === "team"
              ? t("gallery.empty_team")
              : q
              ? t("gallery.empty_search")
              : t("gallery.empty_none")}
          </div>
        )}
      </div>

      {source !== "team" && teamCount === 0 && (
        <div className="mt-5 pt-3 border-t border-line text-[12px] text-faint" data-testid="gallery-team-teaser">
          {t("gallery.team_teaser")}
        </div>
      )}
    </div>
  );

  const card = detail?.card;
  const caps = detail?.capabilities;
  const detailView = detailSlug && (
    <div data-testid="gallery-detail">
      <button
        className="text-[13px] text-muted hover:text-ink mb-3"
        onClick={() => setDetailSlug(null)}
      >
        {t("gallery.back_to_gallery")}
      </button>
      {!detail ? (
        <div className="text-[13px] text-muted">{t("gallery.loading")}</div>
      ) : !detail.ok || !card ? (
        <div className="text-[13px] text-danger">{detail.error || t("gallery.could_not_load")}</div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-start gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-semibold text-[16px]">{card.name}</span>
                <span className={CHIP}>{card.family}</span>
              </div>
              <div className="text-[13px] text-muted">{card.tagline}</div>
              <div className="text-[12px] text-faint mt-1">
                {t("gallery.detail_meta", { version: card.version, publisher: card.publisher, risk: card.risk_summary })}
              </div>
            </div>
            <div className="shrink-0">
              {installed.has(detailSlug) ? (
                <span className="text-[13px] text-muted">{t("gallery.installed")}</span>
              ) : (
                <button className={BTN_ACCENT} onClick={() => install(detailSlug)} disabled={busy}>
                  {busy ? t("gallery.installing") : t("gallery.install")}
                </button>
              )}
            </div>
          </div>
          {msg && <div className="text-[13px] text-danger">{msg}</div>}

          {justInstalled && (
            <div className="rounded-lg border border-okLine bg-okSoft px-3.5 py-2.5 flex items-center gap-3">
              <span className="flex-1 text-[13px] text-ok">
                {t("gallery.installed_waiting")}
              </span>
              <button className={BTN_ACCENT} onClick={onClose}>
                {t("gallery.done")}
              </button>
            </div>
          )}

          <PersonaHero slug={detailSlug} height={128} className="rounded-xl" />

          {card.pitch_markdown && (
            <div className={CARD + " p-4 text-[13px] leading-relaxed"}>
              <Markdown text={card.pitch_markdown} />
            </div>
          )}

          {caps && (
            <div className={CARD + " p-4"} data-testid="gallery-capabilities">
              <div className="text-[13px] font-semibold mb-2">
                {t("gallery.capabilities_title")}
              </div>
              <div className="text-[12px] text-faint mb-3">
                {t("gallery.capabilities_desc")}
              </div>
              <div className="space-y-2 text-[13px]">
                <div>
                  <span className="text-muted">{t("gallery.tools_label")}</span>
                  {caps.tools.join(", ") || t("gallery.none")}
                  {caps.risk.length > 0 && (
                    <span className="text-faint"> {t("gallery.risk_suffix", { risk: caps.risk.join(", ") })}</span>
                  )}
                </div>
                <div>
                  <span className="text-muted">{t("gallery.permissions_label")}</span>
                  {t("gallery.mode_suffix", { mode: caps.recommended_mode })}
                  {caps.messaging ? ` · ${t("gallery.can_message")}` : ""}
                  {caps.mcp.length > 0 ? ` · ${t("gallery.mcp_suffix", { mcp: caps.mcp.join(", ") })}` : ""}
                </div>
                {(detail.recommends?.length ?? 0) > 0 && (
                  <div>
                    <div className="text-muted mb-1.5">{t("gallery.works_with")}</div>
                    <div className="space-y-1.5">
                      {detail.recommends!.map((r) => (
                        <div key={r.kind + r.ref} className="flex items-baseline gap-2">
                          <span className={CHIP + " inline-flex items-center gap-1 shrink-0"}>
                            <BrandIcon name={r.ref} size={12} />
                            {r.ref}
                            {r.tier === "core" ? ` · ${t("gallery.core_tag")}` : ""}
                          </span>
                          <span className="text-[12px] text-faint">{r.reason}</span>
                        </div>
                      ))}
                    </div>
                    <div className="text-[12px] text-faint mt-2">
                      {t("gallery.connect_yourself_note")}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );

  return (
    <div className="fixed inset-0 z-50" data-testid="gallery-modal">
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[1px]" onClick={onClose} />
      <div className="absolute left-1/2 top-[6vh] -translate-x-1/2 w-[720px] max-w-[94vw] max-h-[88vh] rounded-xl2 border border-line bg-panel shadow-2xl overflow-hidden flex flex-col">
        <div className="px-5 pt-4 pb-3 border-b border-line flex items-center gap-3 shrink-0">
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-semibold">{t("gallery.title")}</div>
            <div className="text-[12px] text-muted">
              {t("gallery.subtitle")}
            </div>
          </div>
          {cloud?.signed_in && !detailSlug && (
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("gallery.search_placeholder")}
              className="w-[180px] px-3 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
            />
          )}
          <button
            className="text-faint hover:text-ink shrink-0"
            onClick={onClose}
            aria-label={t("gallery.close_aria")}
            data-testid="gallery-close"
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        <div className="overflow-y-auto hairline-scroll p-5">
          {loading ? (
            <div className="space-y-2" data-testid="gallery-loading" aria-busy="true">
              <div className="text-[13px] text-muted mb-3">{t("gallery.loading_gallery")}</div>
              {[0, 1, 2].map((i) => (
                <div key={i} className={CARD + " p-3.5 animate-pulse"}>
                  <div className="h-3.5 w-44 rounded bg-line mb-2.5" />
                  <div className="h-3 w-72 max-w-full rounded bg-line/60" />
                </div>
              ))}
            </div>
          ) : cloud && !cloud.signed_in ? (
            <div className={CARD + " p-5 flex items-center gap-4"} data-testid="gallery-signin">
              <div className="min-w-0 flex-1">
                <div className="font-semibold text-[14px] mb-1">{t("gallery.signin_title")}</div>
                <div className="text-[13px] text-muted leading-relaxed">
                  {t("gallery.signin_desc")}
                </div>
              </div>
              <button className={BTN_ACCENT} onClick={signIn} disabled={signingIn}>
                {signingIn ? t("gallery.check_browser") : t("gallery.sign_in")}
              </button>
            </div>
          ) : detailSlug ? (
            detailView
          ) : (
            catalog
          )}
        </div>
      </div>
    </div>
  );
}
