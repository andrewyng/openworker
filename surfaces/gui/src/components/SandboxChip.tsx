// The session header's sandbox chip (OPE-218; mock ux-improvements/mocks/
// sandbox-indicator-and-site-card.html, approved 2026-10-02). It says which walls THIS
// session runs behind, which were fixed when the session started:
//   - sandboxed: green, "OpenShell · 3 sites" / "macOS sandbox · any site";
//   - not sandboxed although the machine is set to use a sandbox (the session was opened
//     before the switch): amber, "Not sandboxed", the one state worth a warning;
//   - no sandbox on this machine: no chip at all, since nothing was promised.
// Clicking it lists the session's folders, sites and shared logins.
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "./Icon";

export interface SessionSandbox {
  state: "sandboxed" | "not_sandboxed" | "off";
  provider?: string;
  network?: string; // "allowlist" | "open"
  sites?: string[]; // from Settings ▸ Sandbox
  session_sites?: string[]; // allowed by the person for this session only
  folders?: { path: string; writable: boolean }[];
  logins?: string[];
  started?: boolean;
}

// "github.com:443" reads as "github.com": the port only matters when it is not the web's.
const siteName = (entry: string): string => entry.replace(/:443$/, "");

export function SandboxChip({ info, onOpenSettings }: { info: SessionSandbox | null; onOpenSettings?: () => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", key);
    };
  }, [open]);

  if (!info || info.state === "off") return null;

  const sandboxed = info.state === "sandboxed";
  const providerLabel = t(`sandboxchip.provider_${info.provider}`, { defaultValue: info.provider || "" });
  const sites = info.sites || [];
  const sessionSites = info.session_sites || [];
  const network = !sandboxed
    ? ""
    : info.network === "allowlist"
      ? t("sandboxchip.sites", { count: sites.length + sessionSites.length })
      : t("sandboxchip.any_site");
  const label = sandboxed ? `${providerLabel} · ${network}` : t("sandboxchip.not_sandboxed");
  const tone = sandboxed ? "border-okLine bg-okSoft text-ok" : "border-lineStrong bg-warnSoft text-warnInk";

  return (
    <div className="relative" ref={box} onMouseDown={(e) => e.stopPropagation()}>
      <button
        className={"inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full border text-meta whitespace-nowrap " + tone}
        onClick={() => setOpen((v) => !v)}
        data-testid="sandbox-chip"
        data-state={info.state}
        aria-expanded={open}
      >
        {sandboxed && <Icon name="shield" size={12} />}
        {label}
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-2 z-40 w-[330px] bg-panel border border-line rounded-xl2 shadow-xl p-4 text-left"
          data-testid="sandbox-chip-panel"
        >
          {sandboxed ? (
            <>
              <div className="text-ui font-semibold text-ink">{t("sandboxchip.title")}</div>
              <div className="text-meta text-muted mt-0.5">{t("sandboxchip.sub", { provider: providerLabel })}</div>
              <div className="text-label text-faint mt-3 mb-0.5">{t("sandboxchip.folders")}</div>
              {(info.folders || []).map((f) => (
                <div key={f.path} className="text-meta text-ink font-mono truncate" title={f.path}>
                  {f.path}{" "}
                  <span className="text-faint font-sans">{f.writable ? t("sandboxchip.read_write") : t("sandboxchip.read_only")}</span>
                </div>
              ))}
              <div className="text-label text-faint mt-3 mb-0.5">{t("sandboxchip.sites_label")}</div>
              <div className="text-meta text-ink">
                {info.network === "allowlist"
                  ? sites.length
                    ? sites.map(siteName).join(", ")
                    : t("sandboxchip.no_sites")
                  : t("sandboxchip.any_site_long")}
              </div>
              {info.network === "allowlist" && sessionSites.length > 0 && (
                <>
                  <div className="text-label text-faint mt-3 mb-0.5">{t("sandboxchip.session_sites_label")}</div>
                  <div className="text-meta text-ink" data-testid="sandbox-chip-session-sites">
                    {sessionSites.map(siteName).join(", ")}
                  </div>
                </>
              )}
              {(info.logins || []).length > 0 && (
                <>
                  <div className="text-label text-faint mt-3 mb-0.5">{t("sandboxchip.logins")}</div>
                  <div className="text-meta text-ink">{(info.logins || []).join(", ")}</div>
                </>
              )}
            </>
          ) : (
            <>
              <div className="text-ui font-semibold text-ink">{t("sandboxchip.not_title")}</div>
              <div className="text-meta text-muted mt-0.5">{t("sandboxchip.not_sub")}</div>
            </>
          )}
          {onOpenSettings && (
            <button
              className="mt-3 text-meta text-accent hover:underline"
              onClick={() => {
                setOpen(false);
                onOpenSettings();
              }}
              data-testid="sandbox-chip-settings"
            >
              {t("sandboxchip.open_settings")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
