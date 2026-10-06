// The Access section's Sites group (OPE-219; mock ux-improvements/mocks/
// session-allowed-sites.html, approved 2026-10-03): which sites THIS session's commands and
// web tools reach. Sites from Settings are listed plain, with a hover saying where to
// remove them; sites the person allowed for this session carry a "Session only" tag and can
// be taken back here. The person can allow one more for the session. Shown only for a
// sandboxed session; with "Allow everything" there is nothing to list.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { allowSessionSite, removeSessionSite } from "../api";
import type { SessionSandbox } from "./SandboxChip";

const SEC_H = "text-label text-faint font-medium";
const siteName = (entry: string): string => entry.replace(/:443$/, "");

export function SessionSites({
  sessionId,
  sandbox,
  onSandbox,
  onOpenSettings,
}: {
  sessionId: string;
  sandbox: SessionSandbox | null;
  // The server's answer after a change: the header chip follows the same data.
  onSandbox?: (next: SessionSandbox) => void;
  onOpenSettings?: () => void;
}) {
  const { t } = useTranslation();
  const [adding, setAdding] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!sandbox || sandbox.state !== "sandboxed") return null;
  const allowlist = sandbox.network === "allowlist";
  const sites = sandbox.sites || [];
  const sessionSites = sandbox.session_sites || [];

  const change = async (call: () => Promise<{ ok: boolean; error?: string; sandbox?: SessionSandbox }>) => {
    setBusy(true);
    setError("");
    try {
      const res = await call();
      if (res.sandbox) onSandbox?.(res.sandbox);
      if (!res.ok) setError(res.error || t("access.sites.failed"));
      return res.ok;
    } catch {
      setError(t("access.sites.failed"));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const settingsLink = onOpenSettings && (
    <button className="mt-1 block text-meta text-accent hover:underline text-left" onClick={onOpenSettings} data-testid="session-sites-settings">
      {t("access.sites.open_settings")}
    </button>
  );

  return (
    <div data-testid="session-sites">
      <div className={`${SEC_H} mb-1.5`}>{t("access.sites.title")}</div>
      {!allowlist ? (
        <>
          <div className="text-ui text-ink">{t("access.sites.any_site")}</div>
          <div className="text-meta text-faint mt-0.5">{t("access.sites.any_site_note")}</div>
          {settingsLink}
        </>
      ) : (
        <>
          {sites.length + sessionSites.length === 0 && <div className="text-meta text-faint">{t("access.sites.none")}</div>}
          <div className="-mx-1.5">
            {sites.map((entry) => (
              <div
                key={entry}
                className="px-1.5 py-1 rounded-md font-mono text-meta text-ink truncate"
                title={t("access.sites.settings_hint")}
                data-testid="session-site"
                data-source="settings"
              >
                {siteName(entry)}
              </div>
            ))}
            {sessionSites.map((entry) => (
              <div
                key={entry}
                className="group flex items-center gap-2 px-1.5 py-1 rounded-md hover:bg-paper"
                data-testid="session-site"
                data-source="session"
              >
                <span className="flex-1 min-w-0 truncate font-mono text-meta text-ink">{siteName(entry)}</span>
                <span className="text-label px-1.5 py-0.5 rounded-full border border-accent/25 bg-accentSoft text-accent whitespace-nowrap">
                  {t("access.sites.session_only")}
                </span>
                <button
                  className="text-faint hover:text-ink opacity-0 group-hover:opacity-100 focus:opacity-100 disabled:opacity-40"
                  title={t("access.sites.remove")}
                  aria-label={t("access.sites.remove_named", { host: siteName(entry) })}
                  disabled={busy}
                  onClick={() => void change(() => removeSessionSite(sessionId, entry))}
                  data-testid="session-site-remove"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          {adding ? (
            <form
              className="mt-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                const host = typed.trim();
                if (!host) return;
                void change(() => allowSessionSite(sessionId, host)).then((ok) => {
                  if (ok) {
                    setTyped("");
                    setAdding(false);
                  }
                });
              }}
            >
              <div className="flex gap-1.5">
                <input
                  className="flex-1 min-w-0 px-2 py-1.5 rounded-lg border border-line focus:border-accent bg-paper font-mono text-meta text-ink outline-none"
                  placeholder={t("access.sites.placeholder")}
                  value={typed}
                  onChange={(e) => {
                    setTyped(e.target.value);
                    if (error) setError("");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      setAdding(false);
                      setTyped("");
                      setError("");
                    }
                  }}
                  autoFocus
                  data-testid="session-site-input"
                />
                <button
                  type="submit"
                  className="text-meta px-2.5 py-1.5 rounded-lg bg-accent text-white hover:opacity-90 disabled:opacity-40 shrink-0"
                  disabled={busy || !typed.trim()}
                  data-testid="session-site-allow"
                >
                  {t("access.sites.allow")}
                </button>
              </div>
              <div className="text-label text-faint mt-1">{t("access.sites.add_note")}</div>
            </form>
          ) : (
            <button className="mt-1 block text-meta text-accent hover:underline text-left" onClick={() => setAdding(true)} data-testid="session-site-add">
              + {t("access.sites.add")}
            </button>
          )}
          {error && (
            <div className="roots-err" data-testid="session-sites-error">
              {error}
            </div>
          )}
          {settingsLink}
        </>
      )}
    </div>
  );
}
