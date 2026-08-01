import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createBrowserSettingsClient,
  type BrowserExtensionStatus,
  type BrowserSettings,
  type BrowserSettingsClient,
  type BrowserSettingsUpdate,
  type BrowserSiteAccessMode,
} from "../browser/BrowserSettingsClient";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";
import { Toggle } from "./Toggle";

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_HELP = "text-[12px] text-muted mt-1.5 leading-relaxed";
const BTN_BORDERED =
  "text-[12.5px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong active:translate-y-px shrink-0 disabled:opacity-40";

const SITE_ACCESS_OPTIONS: {
  value: BrowserSiteAccessMode;
  label: string;
  description: string;
}[] = [
  {
    value: "ask",
    label: "Always ask",
    description: "Confirm before opening a site that is not explicitly allowed.",
  },
  {
    value: "auto",
    label: "Auto approve",
    description: "Open routine sites automatically and ask on sensitive destinations.",
  },
  {
    value: "allow",
    label: "Always allow",
    description: "Open sites without routine prompts. Blocked sites remain unavailable.",
  },
];

export function normalizeSiteHost(value: string): string | null {
  const trimmed = value.trim().toLowerCase();
  if (!trimmed) return null;
  if (trimmed.includes("*")) return null;
  try {
    const parsed = new URL(
      /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)
        ? trimmed
        : `https://${trimmed}`,
    );
    if (
      parsed.username ||
      parsed.password ||
      (parsed.pathname !== "/" && parsed.pathname !== "") ||
      parsed.search ||
      parsed.hash
    ) {
      return null;
    }
    const hostname = parsed.hostname.replace(/\.$/, "");
    if (
      !hostname ||
      hostname.length > 253 ||
      (!hostname.includes(".") && hostname !== "localhost") ||
      !/^[a-z0-9.-]+$/i.test(hostname)
    ) {
      return null;
    }
    return hostname;
  } catch {
    return null;
  }
}

export function BrowserSettingsSection({
  client: suppliedClient,
}: {
  client?: BrowserSettingsClient;
}) {
  // This page is intentionally HTTP-only. It never opens a task browser websocket.
  const ownedClient = useMemo(
    () => (suppliedClient ? null : createBrowserSettingsClient()),
    [suppliedClient],
  );
  const client = suppliedClient || ownedClient!;
  const [settings, setSettings] = useState<BrowserSettings | null>(null);
  const [extensionStatus, setExtensionStatus] =
    useState<BrowserExtensionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusLoading, setStatusLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [error, setError] = useState("");
  const [statusError, setStatusError] = useState("");
  const [message, setMessage] = useState("");
  const [downloadDraft, setDownloadDraft] = useState("");
  const [developerArmed, setDeveloperArmed] = useState(false);
  const [clearArmed, setClearArmed] = useState(false);

  const refreshChrome = useCallback(async () => {
    setStatusLoading(true);
    setStatusError("");
    try {
      setExtensionStatus(await client.getBrowserExtensionStatus());
    } catch (statusLoadError) {
      setStatusError(
        statusLoadError instanceof Error
          ? statusLoadError.message
          : "Could not read Chrome connection status.",
      );
    } finally {
      setStatusLoading(false);
    }
  }, [client]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    client
      .getBrowserSettings()
      .then((next) => {
        if (!active) return;
        setSettings(next);
        setDownloadDraft(next.downloadDirectory);
      })
      .catch((loadError) => {
        if (!active) return;
        setError(
          loadError instanceof Error
            ? loadError.message
            : "Could not load browser settings.",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [client, loadAttempt]);

  useEffect(() => {
    void refreshChrome();
    const poll = globalThis.setInterval(() => void refreshChrome(), 5000);
    return () => globalThis.clearInterval(poll);
  }, [refreshChrome]);

  const save = useCallback(
    async (
      patch: BrowserSettingsUpdate,
      successMessage = "Browser settings saved.",
    ): Promise<BrowserSettings | null> => {
      if (!settings || saving) return null;
      setSaving(true);
      setError("");
      setMessage("");
      try {
        const next = await client.updateBrowserSettings(patch);
        setSettings(next);
        setDownloadDraft(next.downloadDirectory);
        setMessage(successMessage);
        return next;
      } catch (saveError) {
        setError(
          saveError instanceof Error
            ? saveError.message
            : "Could not update browser settings.",
        );
        return null;
      } finally {
        setSaving(false);
      }
    },
    [client, saving, settings],
  );

  const selectedMode =
    SITE_ACCESS_OPTIONS.find(
      (option) => option.value === settings?.siteAccessMode,
    ) || SITE_ACCESS_OPTIONS[0];
  const chrome = extensionStatus?.surfaces.find(
    (surface) => surface.surface === "chrome",
  );

  return (
    <section data-testid="browser-settings-page">
      <PanelHead
        title="Browser"
        sub="Website access, saved sessions, downloads, and the Chrome connection used by your coworkers."
      />

      {loading ? (
        <BrowserSettingsSkeleton />
      ) : !settings ? (
        <div className={CARD + " p-5"} role="alert">
          <div className="text-[13.5px] font-medium text-ink">
            Browser settings could not be loaded
          </div>
          <div className="text-[12px] text-muted mt-1.5">
            {error || "The local browser service did not respond."}
          </div>
          <button
            type="button"
            className={BTN_BORDERED + " mt-3"}
            onClick={() => setLoadAttempt((value) => value + 1)}
          >
            Try again
          </button>
        </div>
      ) : (
        <div className="space-y-4" aria-busy={saving}>
          {(error || message) && (
            <div
              className={
                "rounded-lg border px-3 py-2.5 text-[12px] " +
                (error
                  ? "border-red-200 bg-red-50 text-red-700"
                  : "border-line bg-paper text-muted")
              }
              role={error ? "alert" : "status"}
            >
              {error || message}
            </div>
          )}

          <div className={CARD}>
            <div className="p-4">
              <div className="text-[13.5px] font-medium text-ink">
                Website access
              </div>
              <div className={FIELD_HELP}>
                Choose when a coworker must ask before opening a website.
              </div>
              <div
                className="seg mt-3 w-full"
                role="radiogroup"
                aria-label="Website access"
              >
                {SITE_ACCESS_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={settings.siteAccessMode === option.value}
                    className={
                      settings.siteAccessMode === option.value ? "active" : ""
                    }
                    disabled={saving}
                    onClick={() =>
                      void save({ siteAccessMode: option.value })
                    }
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <div className={FIELD_HELP}>{selectedMode.description}</div>
            </div>

            <div className="border-t border-line p-4 grid grid-cols-1 md:grid-cols-2 gap-5">
              <BrowserSiteRules
                kind="allowed"
                values={settings.allowedSites}
                disabled={saving}
                onAdd={(host) =>
                  save(
                    {
                      allowedSites: [...settings.allowedSites, host].sort(),
                      blockedSites: settings.blockedSites.filter(
                        (site) => site !== host,
                      ),
                    },
                    `${host} is allowed.`,
                  )
                }
                onRemove={(host) =>
                  save(
                    {
                      allowedSites: settings.allowedSites.filter(
                        (site) => site !== host,
                      ),
                    },
                    `${host} removed from allowed sites.`,
                  )
                }
              />
              <BrowserSiteRules
                kind="blocked"
                values={settings.blockedSites}
                disabled={saving}
                onAdd={(host) =>
                  save(
                    {
                      blockedSites: [...settings.blockedSites, host].sort(),
                      allowedSites: settings.allowedSites.filter(
                        (site) => site !== host,
                      ),
                    },
                    `${host} is blocked.`,
                  )
                }
                onRemove={(host) =>
                  save(
                    {
                      blockedSites: settings.blockedSites.filter(
                        (site) => site !== host,
                      ),
                    },
                    `${host} removed from blocked sites.`,
                  )
                }
              />
            </div>
          </div>

          <div className={CARD}>
            <div className="p-4 flex items-center gap-4">
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium text-ink">
                  Remember sign-ins
                </div>
                <div className={FIELD_HELP}>
                  Encrypt website sessions separately from your personal browser.
                </div>
              </div>
              <Toggle
                checked={settings.rememberSignins}
                disabled={saving}
                title="Remember sign-ins"
                onChange={(rememberSignins) =>
                  void save(
                    { rememberSignins },
                    rememberSignins
                      ? "Sign-ins will be remembered."
                      : "New sign-ins will not be saved.",
                  )
                }
              />
            </div>
            <div className="border-t border-line px-4 py-3 flex items-center gap-3">
              <div className="min-w-0 flex-1 text-[12px] text-muted">
                Remove cookies, sign-ins, and saved website data from the OpenWorker Browser.
              </div>
              {clearArmed && (
                <button
                  type="button"
                  className={BTN_BORDERED}
                  disabled={saving}
                  onClick={() => {
                    setClearArmed(false);
                    setMessage("");
                  }}
                >
                  Cancel
                </button>
              )}
              <button
                type="button"
                className={
                  clearArmed
                    ? "text-[12.5px] px-3 py-2 rounded-lg bg-red-600 text-white disabled:opacity-40"
                    : BTN_BORDERED
                }
                disabled={saving}
                onClick={async () => {
                  if (!clearArmed) {
                    setClearArmed(true);
                    setMessage("Clear cookies, sign-ins, and saved website data?");
                    return;
                  }
                  setSaving(true);
                  setError("");
                  setMessage("");
                  try {
                    await client.clearBrowserData();
                    setClearArmed(false);
                    setMessage("Browser data cleared.");
                  } catch (clearError) {
                    setError(
                      clearError instanceof Error
                        ? clearError.message
                        : "Could not clear browser data.",
                    );
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                {clearArmed ? "Confirm clear" : "Clear browser data"}
              </button>
            </div>
          </div>

          <div className={CARD}>
            <div className="p-4 flex items-start gap-3">
              <div className="mt-0.5 w-8 h-8 rounded-lg bg-paper border border-line grid place-items-center text-muted">
                <Icon name="plug" size={16} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium text-ink">
                  Google Chrome
                </div>
                <div className={FIELD_HELP}>
                  Work in signed-in tabs you explicitly share from the OpenWorker Chrome extension.
                </div>
                <div className="mt-3 flex items-center gap-2 text-[12.5px]">
                  <span
                    className={
                      "w-2 h-2 rounded-full " +
                      (chrome?.connected ? "bg-green-500" : "bg-muted/40")
                    }
                    aria-hidden="true"
                  />
                  <span className="font-medium text-ink">
                    {statusLoading && !extensionStatus
                      ? "Checking connection…"
                      : chrome?.connected
                        ? "Chrome extension connected"
                        : "Chrome extension not connected"}
                  </span>
                  {chrome?.connected && (
                    <span className="text-muted">
                      · {chrome.claimedTabs} {chrome.claimedTabs === 1 ? "tab" : "tabs"} shared
                    </span>
                  )}
                </div>
                {!chrome?.connected && !statusLoading && !statusError && (
                  <div className="text-[12px] text-muted mt-1.5">
                    Install or enable the OpenWorker extension in Chrome. It reconnects automatically.
                  </div>
                )}
                {statusError && (
                  <div className="text-[12px] text-red-600 mt-1.5" role="alert">
                    {statusError}
                  </div>
                )}
              </div>
              <button
                type="button"
                className={BTN_BORDERED}
                disabled={statusLoading}
                onClick={() => void refreshChrome()}
                aria-label="Refresh Chrome connection"
              >
                <Icon name="refresh" size={13} />
              </button>
            </div>
          </div>

          <div className={CARD}>
            <div className="p-4">
              <div className="text-[13.5px] font-medium text-ink">Downloads</div>
              <div className={FIELD_HELP}>
                Choose where files downloaded by the browser are stored.
              </div>
            </div>
            <div className="border-t border-line p-4 flex items-center gap-4">
              <div className="min-w-0 flex-1">
                <div className="text-[13px] text-ink">Ask where to save each file</div>
                <div className={FIELD_HELP}>
                  Show a location prompt before each download.
                </div>
              </div>
              <Toggle
                checked={settings.askDownloadLocation}
                disabled={saving}
                title="Ask where to save each file"
                onChange={(askDownloadLocation) =>
                  void save(
                    { askDownloadLocation },
                    askDownloadLocation
                      ? "The browser will ask where to save downloads."
                      : "Downloads will use the selected folder.",
                  )
                }
              />
            </div>
            <form
              className="border-t border-line p-4"
              onSubmit={(event) => {
                event.preventDefault();
                void save(
                  { downloadDirectory: downloadDraft.trim() },
                  "Download folder updated.",
                );
              }}
            >
              <label
                className="block text-[12.5px] font-medium text-ink"
                htmlFor="browser-download-directory"
              >
                Download folder
              </label>
              <div className="flex items-center gap-2 mt-2">
                <input
                  id="browser-download-directory"
                  className="flex-1 min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
                  value={downloadDraft}
                  disabled={saving}
                  spellCheck={false}
                  placeholder="System Downloads folder"
                  onChange={(event) => setDownloadDraft(event.target.value)}
                />
                <button
                  type="submit"
                  className="text-[12.5px] px-3 py-2 rounded-lg bg-accent text-white disabled:opacity-40"
                  disabled={
                    saving ||
                    downloadDraft.trim() === settings.downloadDirectory
                  }
                >
                  Save
                </button>
              </div>
            </form>
          </div>

          <div className={CARD + " p-4"}>
            <div className="flex items-center gap-4">
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium text-ink">
                  Developer mode
                </div>
                <div className={FIELD_HELP}>
                  Allow advanced page inspection and debugging tools on sites you trust.
                </div>
              </div>
              <Toggle
                checked={settings.developerMode}
                disabled={saving}
                title="Developer mode"
                onChange={(developerMode) => {
                  if (developerMode) {
                    setDeveloperArmed(true);
                    setMessage("");
                    return;
                  }
                  void save(
                    { developerMode: false },
                    "Developer mode disabled.",
                  );
                }}
              />
            </div>
            {developerArmed && !settings.developerMode && (
              <div
                className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3"
                role="alert"
              >
                <div className="text-[12.5px] font-medium text-amber-900">
                  Enable advanced browser access?
                </div>
                <div className="text-[12px] text-amber-800 mt-1">
                  Developer mode can expose page internals. Use it only on sites you trust.
                </div>
                <div className="mt-3 flex justify-end gap-2">
                  <button
                    type="button"
                    className={BTN_BORDERED}
                    disabled={saving}
                    onClick={() => setDeveloperArmed(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="text-[12.5px] px-3 py-2 rounded-lg bg-accent text-white disabled:opacity-40"
                    disabled={saving}
                    onClick={async () => {
                      const next = await save(
                        { developerMode: true },
                        "Developer mode enabled.",
                      );
                      if (next) setDeveloperArmed(false);
                    }}
                  >
                    Enable
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function BrowserSettingsSkeleton() {
  return (
    <div className="space-y-4" aria-label="Loading browser settings">
      {[176, 104, 124].map((height) => (
        <div
          key={height}
          className={CARD + " p-4 animate-pulse"}
          style={{ minHeight: `${height}px` }}
        >
          <div className="h-3.5 w-32 rounded bg-line" />
          <div className="h-3 w-64 max-w-full rounded bg-line/70 mt-3" />
        </div>
      ))}
    </div>
  );
}

function BrowserSiteRules({
  kind,
  values,
  disabled,
  onAdd,
  onRemove,
}: {
  kind: "allowed" | "blocked";
  values: string[];
  disabled: boolean;
  onAdd: (host: string) => Promise<BrowserSettings | null>;
  onRemove: (host: string) => Promise<BrowserSettings | null>;
}) {
  const [draft, setDraft] = useState("");
  const [validationError, setValidationError] = useState("");
  const title = kind === "allowed" ? "Allowed sites" : "Blocked sites";

  return (
    <div>
      <div className="text-[12.5px] font-medium text-ink">{title}</div>
      <div className={FIELD_HELP}>
        {kind === "allowed"
          ? "These sites can open without a routine prompt."
          : "Coworkers cannot open these sites."}
      </div>

      {values.length ? (
        <ul className="mt-3 divide-y divide-line" aria-label={title}>
          {values.map((host) => (
            <li key={host} className="py-2 flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink" title={host}>
                {host}
              </span>
              <button
                type="button"
                className="p-1 rounded text-muted hover:text-ink hover:bg-paper"
                disabled={disabled}
                onClick={() => void onRemove(host)}
                aria-label={`Remove ${host} from ${title.toLowerCase()}`}
              >
                <Icon name="x" size={12} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-3 text-[12px] text-muted">
          No {kind} site exceptions.
        </div>
      )}

      <form
        className="mt-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const host = normalizeSiteHost(draft);
          if (!host) {
            setValidationError("Enter a hostname such as example.com.");
            return;
          }
          if (values.includes(host)) {
            setValidationError(`${host} is already in this list.`);
            return;
          }
          setValidationError("");
          const next = await onAdd(host);
          if (next) setDraft("");
        }}
      >
        <label className="sr-only" htmlFor={`browser-${kind}-site`}>
          Add to {title.toLowerCase()}
        </label>
        <div className="flex items-center gap-2">
          <input
            id={`browser-${kind}-site`}
            className="flex-1 min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
            value={draft}
            disabled={disabled}
            autoCapitalize="none"
            autoComplete="off"
            spellCheck={false}
            placeholder="example.com"
            aria-invalid={Boolean(validationError)}
            aria-describedby={
              validationError ? `browser-${kind}-site-error` : undefined
            }
            onChange={(event) => {
              setDraft(event.target.value);
              if (validationError) setValidationError("");
            }}
          />
          <button
            type="submit"
            className={BTN_BORDERED}
            disabled={disabled || !draft.trim()}
          >
            Add
          </button>
        </div>
        {validationError && (
          <div
            id={`browser-${kind}-site-error`}
            className="text-[11.5px] text-red-600 mt-1.5"
            role="alert"
          >
            {validationError}
          </div>
        )}
      </form>
    </div>
  );
}
