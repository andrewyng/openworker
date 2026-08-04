import { useEffect, useState } from "react";
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
  "text-[12.5px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const CHIP = "text-[10.5px] px-1.5 py-0.5 rounded border border-line text-muted";

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
    setDetail(d ?? { ok: false, error: "无法加载详情" });
  };

  const install = async (slug: string) => {
    setBusy(true);
    setMsg(null);
    const r = await installPersona({ gallery_slug: slug });
    setBusy(false);
    if (!r.ok) {
      setMsg(r.error || "安装失败");
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
            ["all", "全部"],
            ["openworker", "来自 OpenWorker"],
            ["team", "来自你的团队"],
          ] as [Source, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            className={
              "text-[11.5px] px-2.5 py-1 rounded-full border " +
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
        <div className="text-[12.5px] text-muted">
          图库暂时无法访问——请稍后再试。
        </div>
      )}

      {featured.length > 0 && (
        <>
          <div className="text-[11px] uppercase tracking-[0.05em] text-faint font-semibold mb-2">
            精选
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
        全部角色
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
                  <span className="font-semibold text-[13.5px]">{p.name}</span>
                  <span className={CHIP}>{p.family}</span>
                  <span className="text-[11px] text-faint">
                    v{p.version} · {p.publisher}
                  </span>
                </div>
                <div className="text-[12.5px] text-muted mb-1.5">{p.tagline}</div>
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
                  <span className="text-[12px] text-muted">已安装</span>
                ) : (
                  <span className="text-[12.5px] text-accent">查看并安装 →</span>
                )}
              </div>
            </div>
          );
        })}
        {visible.length === 0 && !unavailable && (
          <div className="text-[12.5px] text-muted py-4">
            {source === "team"
              ? "还没有分享给你团队的角色。"
              : q
              ? "没有匹配你搜索的角色。"
              : "还没有发布任何角色。"}
          </div>
        )}
      </div>

      {source !== "team" && teamCount === 0 && (
        <div className="mt-5 pt-3 border-t border-line text-[12px] text-faint" data-testid="gallery-team-teaser">
          来自你的团队——目前还没有分享。向团队成员发布角色的功能即将推出。
        </div>
      )}
    </div>
  );

  const card = detail?.card;
  const caps = detail?.capabilities;
  const detailView = detailSlug && (
    <div data-testid="gallery-detail">
      <button
        className="text-[12.5px] text-muted hover:text-ink mb-3"
        onClick={() => setDetailSlug(null)}
      >
        ← 图库
      </button>
      {!detail ? (
        <div className="text-[12.5px] text-muted">加载中…</div>
      ) : !detail.ok || !card ? (
        <div className="text-[12.5px] text-danger">{detail.error || "无法加载详情"}</div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-start gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-semibold text-[17px]">{card.name}</span>
                <span className={CHIP}>{card.family}</span>
              </div>
              <div className="text-[13px] text-muted">{card.tagline}</div>
              <div className="text-[11.5px] text-faint mt-1">
                v{card.version} · {card.publisher} · {card.risk_summary}
              </div>
            </div>
            <div className="shrink-0">
              {installed.has(detailSlug) ? (
                <span className="text-[12.5px] text-muted">已安装</span>
              ) : (
                <button className={BTN_ACCENT} onClick={() => install(detailSlug)} disabled={busy}>
                  {busy ? "安装中…" : "安装"}
                </button>
              )}
            </div>
          </div>
          {msg && <div className="text-[12.5px] text-danger">{msg}</div>}

          {justInstalled && (
            <div className="rounded-lg border border-okLine bg-okSoft px-3.5 py-2.5 flex items-center gap-3">
              <span className="flex-1 text-[12.5px] text-ok">
                已安装——它正在“角色”中等待，在你确认并启用之前保持停用状态。
              </span>
              <button className={BTN_ACCENT} onClick={onClose}>
                完成
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
                它能做什么——已从其 manifest 验证
              </div>
              <div className="text-[12px] text-faint mb-3">
                由本应用自己的解析器读取，因此与安装时授权页要求你批准的内容完全一致。不会安装任何可执行代码。
              </div>
              <div className="space-y-2 text-[12.5px]">
                <div>
                  <span className="text-muted">工具：</span>
                  {caps.tools.join(", ") || "无"}
                  {caps.risk.length > 0 && (
                    <span className="text-faint"> · 风险：{caps.risk.join(", ")}</span>
                  )}
                </div>
                <div>
                  <span className="text-muted">权限：</span>
                  {caps.recommended_mode} 模式
                  {caps.messaging ? " · 可使用消息" : ""}
                  {caps.mcp.length > 0 ? ` · MCP: ${caps.mcp.join(", ")}` : ""}
                </div>
                {(detail.recommends?.length ?? 0) > 0 && (
                  <div>
                    <div className="text-muted mb-1.5">可搭配以下连接使用：</div>
                    <div className="space-y-1.5">
                      {detail.recommends!.map((r) => (
                        <div key={r.kind + r.ref} className="flex items-baseline gap-2">
                          <span className={CHIP + " inline-flex items-center gap-1 shrink-0"}>
                            <BrandIcon name={r.ref} size={12} />
                            {r.ref}
                            {r.tier === "core" ? " · 核心" : ""}
                          </span>
                          <span className="text-[12px] text-faint">{r.reason}</span>
                        </div>
                      ))}
                    </div>
                    <div className="text-[11.5px] text-faint mt-2">
                      这些连接由你自己完成（登录后一键即可）——在你连接之前，安装这个 Coworker 不会授予它任何权限。
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
            <div className="text-[15px] font-semibold">角色图库</div>
            <div className="text-[12px] text-muted">
              精选 Coworker · 安装后在你确认前保持停用
            </div>
          </div>
          {cloud?.signed_in && !detailSlug && (
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索角色"
              className="w-[180px] px-3 py-1.5 rounded-lg border border-line bg-paper text-[12.5px] text-ink outline-none focus:border-accent"
            />
          )}
          <button
            className="text-faint hover:text-ink shrink-0"
            onClick={onClose}
            aria-label="关闭图库"
            data-testid="gallery-close"
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        <div className="overflow-y-auto hairline-scroll p-5">
          {loading ? (
            <div className="space-y-2" data-testid="gallery-loading" aria-busy="true">
              <div className="text-[12.5px] text-muted mb-3">正在加载图库…</div>
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
                <div className="font-semibold text-[14px] mb-1">登录后浏览图库</div>
                <div className="text-[12.5px] text-muted leading-relaxed">
                  图库是一组来自 OpenWorker Cloud 的精选 Coworker，需要（免费的）云登录。从文件夹或 Git URL 安装角色——在“角色”页面——始终无需账号即可使用。
                </div>
              </div>
              <button className={BTN_ACCENT} onClick={signIn} disabled={signingIn}>
                {signingIn ? "请查看浏览器…" : "登录"}
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
