import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  BrowserExtensionStatus,
  BrowserProfileState,
  BrowserSettings,
  BrowserSettingsClient,
  BrowserSettingsUpdate,
} from "../browser/BrowserSettingsClient";
import {
  BrowserSettingsSection,
  normalizeSiteHost,
} from "./BrowserSettingsSection";
import { SettingsView } from "./SettingsView";

class FakeBrowserSettingsClient implements BrowserSettingsClient {
  settings: BrowserSettings = {
    siteAccessMode: "ask",
    allowedSites: [],
    blockedSites: ["blocked.example"],
    rememberSignins: false,
    downloadDirectory: "/Users/test/Downloads",
    askDownloadLocation: false,
    developerMode: false,
  };

  profile: BrowserProfileState = {
    rememberSignins: false,
    hasSavedData: true,
  };

  extensionStatus: BrowserExtensionStatus = {
    selectedSurface: "iab",
    surfaces: [
      {
        surface: "chrome",
        label: "Google Chrome",
        connected: false,
        available: false,
        claimedTabs: 0,
        client: {},
        disconnectReason: "",
        nativeHostInstalled: true,
        extensionId: "chrome-extension-id",
      },
    ],
  };

  updates: BrowserSettingsUpdate[] = [];

  async getProfile() {
    return this.profile;
  }

  async setRememberSignins(rememberSignins: boolean) {
    this.profile = { ...this.profile, rememberSignins };
    return this.profile;
  }

  async clearBrowserData() {
    this.profile = { ...this.profile, hasSavedData: false };
    return this.profile;
  }

  async getBrowserSettings() {
    return this.settings;
  }

  async updateBrowserSettings(update: BrowserSettingsUpdate) {
    this.updates.push(update);
    this.settings = { ...this.settings, ...update };
    return this.settings;
  }

  async getBrowserExtensionStatus() {
    return this.extensionStatus;
  }
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Settings → Browser", () => {
  it("accepts exact hostnames and rejects wildcard site rules", () => {
    expect(normalizeSiteHost("https://Docs.Example.com")).toBe(
      "docs.example.com",
    );
    expect(normalizeSiteHost("*.example.com")).toBeNull();
    expect(normalizeSiteHost("https://*.example.com")).toBeNull();
  });

  it("is a first-class settings destination without creating a browser websocket", async () => {
    const websocket = vi.fn();
    vi.stubGlobal("WebSocket", websocket);
    const client = new FakeBrowserSettingsClient();

    render(<SettingsView initialTab="browser" browserClient={client} />);

    expect(screen.getByRole("button", { name: /Browser/ })).toBeTruthy();
    expect(await screen.findByTestId("browser-settings-page")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Browser" })).toBeTruthy();
    expect(websocket).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("manages persistent site, privacy, download, and developer settings", async () => {
    const client = new FakeBrowserSettingsClient();
    render(<BrowserSettingsSection client={client} />);

    expect(await screen.findByRole("radio", { name: "Always ask" })).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Auto approve" }));
    await waitFor(() => expect(client.settings.siteAccessMode).toBe("auto"));

    const allowed = screen.getByLabelText("Add to allowed sites");
    fireEvent.change(allowed, { target: { value: "https://docs.example.com" } });
    fireEvent.submit(allowed.closest("form")!);
    await waitFor(() =>
      expect(client.settings.allowedSites).toEqual(["docs.example.com"]),
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Remove blocked.example from blocked sites",
      }),
    );
    await waitFor(() => expect(client.settings.blockedSites).toEqual([]));

    fireEvent.click(screen.getByRole("switch", { name: "Remember sign-ins" }));
    await waitFor(() => expect(client.settings.rememberSignins).toBe(true));

    fireEvent.click(
      screen.getByRole("switch", { name: "Ask where to save each file" }),
    );
    await waitFor(() => expect(client.settings.askDownloadLocation).toBe(true));

    const directory = screen.getByLabelText("Download folder");
    fireEvent.change(directory, {
      target: { value: "/Users/test/Desktop/Browser downloads" },
    });
    fireEvent.submit(directory.closest("form")!);
    await waitFor(() =>
      expect(client.settings.downloadDirectory).toBe(
        "/Users/test/Desktop/Browser downloads",
      ),
    );

    fireEvent.click(screen.getByRole("switch", { name: "Developer mode" }));
    expect(screen.getByText("Enable advanced browser access?")).toBeTruthy();
    expect(client.settings.developerMode).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));
    await waitFor(() => expect(client.settings.developerMode).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Clear browser data" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm clear" }));
    await waitFor(() => expect(client.profile.hasSavedData).toBe(false));
  });

  it("shows Chrome-only automatic connection status without setup secrets", async () => {
    const client = new FakeBrowserSettingsClient();
    render(<BrowserSettingsSection client={client} />);

    expect(await screen.findByText("Chrome extension not connected")).toBeTruthy();
    expect(screen.getByText(/reconnects automatically/i)).toBeTruthy();
    expect(screen.queryByText(/pairing code/i)).toBeNull();
    expect(screen.queryByText(/bridge url/i)).toBeNull();
    expect(screen.queryByText(/extension folder/i)).toBeNull();
    expect(screen.queryByText(/Edge/i)).toBeNull();
  });

  it("renders a retryable settings error", async () => {
    const client = new FakeBrowserSettingsClient();
    client.getBrowserSettings = vi
      .fn()
      .mockRejectedValueOnce(new Error("Settings service unavailable"))
      .mockResolvedValue(client.settings);

    render(<BrowserSettingsSection client={client} />);
    expect(
      await screen.findByText("Browser settings could not be loaded"),
    ).toBeTruthy();
    expect(screen.getByText("Settings service unavailable")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("radio", { name: "Always ask" })).toBeTruthy();
  });
});
