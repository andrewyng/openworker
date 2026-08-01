export type BrowserSiteAccessMode = "ask" | "auto" | "allow";

export interface BrowserProfileState {
  rememberSignins: boolean;
  hasSavedData: boolean;
}

export interface BrowserSettings {
  siteAccessMode: BrowserSiteAccessMode;
  allowedSites: string[];
  blockedSites: string[];
  rememberSignins: boolean;
  downloadDirectory: string;
  askDownloadLocation: boolean;
  developerMode: boolean;
}

export type BrowserSettingsUpdate = Partial<BrowserSettings>;

export type ExternalBrowserKind = "chrome";
export type BrowserSurface = "iab" | ExternalBrowserKind;

export interface BrowserExtensionSurface {
  surface: BrowserSurface;
  label: string;
  connected: boolean;
  available: boolean;
  claimedTabs: number;
  client: Record<string, unknown>;
  disconnectReason: string;
  nativeHostInstalled: boolean;
  extensionId: string;
}

export interface BrowserExtensionStatus {
  surfaces: BrowserExtensionSurface[];
  selectedSurface: BrowserSurface;
}

export interface BrowserSurfaceSelection {
  surface: BrowserSurface;
  available: boolean;
}

interface JsonRecord {
  [key: string]: unknown;
}

export const browserHttpBase = (): string =>
  (globalThis as any).__COWORKER_HTTP__ ||
  (import.meta as any).env?.VITE_COWORKER_HTTP ||
  "http://127.0.0.1:8765";

const apiToken = (): string =>
  (globalThis as any).__COWORKER_API_TOKEN__ ||
  (import.meta as any).env?.VITE_COWORKER_API_TOKEN ||
  "";

export const authenticatedBrowserFetch = (
  input: RequestInfo | URL,
  init: RequestInit = {},
) => {
  const headers = new Headers(init.headers);
  const token = apiToken();
  if (token) headers.set("X-OpenWorker-Token", token);
  return globalThis.fetch(input, { ...init, headers });
};

export function postBrowserJson(
  path: string,
  body: JsonRecord,
): Promise<Response> {
  return authenticatedBrowserFetch(`${browserHttpBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function browserRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" ? (value as JsonRecord) : {};
}

export function browserNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function browserText(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function browserBool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

export function normalizeBrowserSettings(raw: unknown): BrowserSettings {
  const root = browserRecord(raw);
  const settings = Object.keys(browserRecord(root.settings)).length
    ? browserRecord(root.settings)
    : root;
  const rawMode = browserText(
    settings.site_access_mode ?? settings.siteAccessMode,
    "ask",
  );
  const siteAccessMode: BrowserSiteAccessMode =
    rawMode === "auto" || rawMode === "allow" ? rawMode : "ask";
  return {
    siteAccessMode,
    allowedSites: stringList(
      settings.allowed_hosts ?? settings.allowed_sites ?? settings.allowedSites,
    ),
    blockedSites: stringList(
      settings.blocked_hosts ?? settings.blocked_sites ?? settings.blockedSites,
    ),
    rememberSignins: browserBool(
      settings.remember_signins ?? settings.rememberSignins,
    ),
    downloadDirectory: browserText(
      settings.download_directory ?? settings.downloadDirectory,
    ),
    askDownloadLocation: browserBool(
      settings.ask_download_location ?? settings.askDownloadLocation,
    ),
    developerMode: browserBool(
      settings.developer_mode ?? settings.developerMode,
    ),
  };
}

export function normalizeBrowserExtensionStatus(
  raw: unknown,
  fallbackSurface: BrowserSurface = "iab",
): BrowserExtensionStatus {
  const root = browserRecord(raw);
  const chrome = browserRecord(root.chrome);
  const surfaces = Array.isArray(root.surfaces)
    ? root.surfaces
    : Object.keys(chrome).length
      ? [{ surface: "chrome", ...chrome }]
      : [];
  const rawSelected = browserText(
    root.selected_surface ?? root.selectedSurface,
    browserBool(chrome.selected_for_task ?? chrome.selectedForTask)
      ? "chrome"
      : fallbackSurface,
  ).toLowerCase();
  return {
    // The product supports the built-in browser and Google Chrome only. Ignore
    // browser kinds an older sidecar may still advertise.
    surfaces: surfaces.flatMap((value): BrowserExtensionSurface[] => {
      const surface = browserRecord(value);
      const name = browserText(surface.surface).toLowerCase();
      if (name !== "iab" && name !== "chrome") return [];
      return [
        {
          surface: name,
          label: browserText(
            surface.label,
            name === "iab" ? "OpenWorker Browser" : "Google Chrome",
          ),
          connected: browserBool(surface.connected),
          available: browserBool(surface.available),
          claimedTabs: Math.max(
            0,
            Math.round(
              browserNumber(
                surface.shared_tab_count ??
                  surface.claimed_tabs ??
                  surface.claimedTabs,
              ),
            ),
          ),
          client: browserRecord(surface.client),
          disconnectReason: browserText(
            surface.disconnect_reason ?? surface.disconnectReason,
          ),
          nativeHostInstalled: browserBool(
            surface.native_host_installed ?? surface.nativeHostInstalled,
          ),
          extensionId: browserText(
            surface.extension_id ?? surface.extensionId,
          ),
        },
      ];
    }),
    selectedSurface: rawSelected === "chrome" ? "chrome" : "iab",
  };
}

export interface BrowserSettingsClient {
  getProfile(): Promise<BrowserProfileState>;
  setRememberSignins(remember: boolean): Promise<BrowserProfileState>;
  clearBrowserData(): Promise<BrowserProfileState>;
  getBrowserSettings(): Promise<BrowserSettings>;
  updateBrowserSettings(
    settings: BrowserSettingsUpdate,
  ): Promise<BrowserSettings>;
  getBrowserExtensionStatus(): Promise<BrowserExtensionStatus>;
}

export class LocalBrowserSettingsClient implements BrowserSettingsClient {
  constructor(private readonly sessionId = "") {}

  async getProfile(): Promise<BrowserProfileState> {
    const response = await authenticatedBrowserFetch(
      `${browserHttpBase()}/v1/browser/profile`,
    );
    const data = browserRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(
        browserText(data.error, "Could not read browser privacy settings."),
      );
    }
    return {
      rememberSignins: browserBool(
        data.remember_signins ?? data.rememberSignins,
      ),
      hasSavedData: browserBool(data.has_saved_data ?? data.hasSavedData),
    };
  }

  async setRememberSignins(
    remember: boolean,
  ): Promise<BrowserProfileState> {
    const response = await postBrowserJson("/v1/browser/profile", {
      remember_signins: remember,
    });
    const data = browserRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(
        browserText(data.error, "Could not update browser privacy settings."),
      );
    }
    return {
      rememberSignins: browserBool(
        data.remember_signins ?? data.rememberSignins,
        remember,
      ),
      hasSavedData: browserBool(data.has_saved_data ?? data.hasSavedData),
    };
  }

  async clearBrowserData(): Promise<BrowserProfileState> {
    const response = await postBrowserJson("/v1/browser/profile", {
      clear_browser_data: true,
    });
    const data = browserRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(
        browserText(data.error, "Could not clear browser data."),
      );
    }
    return {
      rememberSignins: browserBool(
        data.remember_signins ?? data.rememberSignins,
      ),
      hasSavedData: false,
    };
  }

  async getBrowserSettings(): Promise<BrowserSettings> {
    const response = await authenticatedBrowserFetch(
      `${browserHttpBase()}/v1/browser/settings`,
    );
    const data = browserRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(
        browserText(data.error, "Could not read browser settings."),
      );
    }
    return normalizeBrowserSettings(data);
  }

  async updateBrowserSettings(
    settings: BrowserSettingsUpdate,
  ): Promise<BrowserSettings> {
    const body: JsonRecord = {};
    if (settings.siteAccessMode !== undefined) {
      body.site_access_mode = settings.siteAccessMode;
    }
    if (settings.allowedSites !== undefined) {
      body.allowed_hosts = settings.allowedSites;
    }
    if (settings.blockedSites !== undefined) {
      body.blocked_hosts = settings.blockedSites;
    }
    if (settings.rememberSignins !== undefined) {
      body.remember_signins = settings.rememberSignins;
    }
    if (settings.downloadDirectory !== undefined) {
      body.download_directory = settings.downloadDirectory;
    }
    if (settings.askDownloadLocation !== undefined) {
      body.ask_download_location = settings.askDownloadLocation;
    }
    if (settings.developerMode !== undefined) {
      body.developer_mode = settings.developerMode;
    }

    const response = await postBrowserJson("/v1/browser/settings", body);
    const data = browserRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(
        browserText(data.error, "Could not update browser settings."),
      );
    }
    return normalizeBrowserSettings(data);
  }

  async getBrowserExtensionStatus(): Promise<BrowserExtensionStatus> {
    const query = this.sessionId
      ? `?session_id=${encodeURIComponent(this.sessionId)}`
      : "";
    const response = await authenticatedBrowserFetch(
      `${browserHttpBase()}/v1/browser-extension/status${query}`,
    );
    const data = browserRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(
        browserText(
          data.message ?? data.error,
          "Could not read Chrome connection status.",
        ),
      );
    }
    return normalizeBrowserExtensionStatus(data);
  }
}

export function createBrowserSettingsClient(
  sessionId = "",
): BrowserSettingsClient {
  return new LocalBrowserSettingsClient(sessionId);
}
