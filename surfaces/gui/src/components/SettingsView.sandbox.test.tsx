// Settings ▸ Sandbox (UX-051 A): the page shows what the machine reports and writes back
// the three kinds of change: provider, network profile, credential list.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const snapshot = {
  platform: "darwin",
  provider: "",
  effective_provider: "direct",
  refused: "",
  providers: [
    { name: "direct", usable: true, why: "" },
    { name: "seatbelt", usable: true, why: "" },
    { name: "openshell", usable: false, why: "OpenShell is not installed" },
  ],
  network_profile: "strict",
  network_profiles: [
    { name: "strict", hosts: ["github.com"] },
    { name: "standard", hosts: ["github.com", "api.tavily.com"] },
  ],
  credentials: [
    { name: "ssh", path: "~/.ssh", hosts: ["github.com:22"], enabled: false },
    { name: "gh", path: "~/.config/gh", hosts: ["api.github.com:443"], enabled: true },
  ],
  config_path: "/Users/sam/.config/coworker/config.toml",
};

const setSandboxSettings = vi.fn(async (patch: any) => ({ ok: true, ...snapshot, ...patch }));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getSandboxSettings: vi.fn(async () => snapshot),
    setSandboxSettings: (patch: any) => setSandboxSettings(patch),
    getMachines: vi.fn(async () => ({ machines: [] })),
    getCloudMachines: vi.fn(async () => ({ machines: [] })),
    getCloudConnections: vi.fn(async () => []),
    getConnectors: vi.fn(async () => []),
    getCloudStatus: vi.fn(async () => ({ signed_in: false })),
    isCloudMode: () => false,
  };
});

import { SettingsView } from "./SettingsView";

describe("Settings ▸ Sandbox", () => {
  beforeEach(() => setSandboxSettings.mockClear());
  afterEach(cleanup);

  it("shows the providers, the allow list and the credential rows from the machine", async () => {
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    expect((screen.getByTestId("sandbox-provider-direct") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("sandbox-provider-openshell") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("default")).toBeTruthy();
    expect((screen.getByTestId("sandbox-network-strict") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("SSH keys")).toBeTruthy();
    expect((screen.getByLabelText("GitHub CLI") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/Also allows github.com:22/)).toBeTruthy();
    expect(screen.getByText(/config\.toml/)).toBeTruthy();
  });

  it("writes a provider change, a profile change and a credential switch", async () => {
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByTestId("sandbox-provider-seatbelt"));
    await waitFor(() => expect(setSandboxSettings).toHaveBeenCalledWith({ provider: "seatbelt" }));
    fireEvent.click(screen.getByTestId("sandbox-network-standard"));
    await waitFor(() => expect(setSandboxSettings).toHaveBeenCalledWith({ network_profile: "standard" }));
    fireEvent.click(screen.getByLabelText("SSH keys"));
    await waitFor(() =>
      expect(setSandboxSettings).toHaveBeenLastCalledWith({
        credentials: [
          { name: "ssh", path: "~/.ssh", hosts: ["github.com:22"], enabled: true },
          { name: "gh", path: "~/.config/gh", hosts: ["api.github.com:443"], enabled: true },
        ],
      }),
    );
  });

  it("adds an entry through the editor and removes one", async () => {
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByTestId("sandbox-credential-add"));
    const editor = screen.getByTestId("sandbox-credential-editor");
    const inputs = editor.querySelectorAll("input, textarea");
    fireEvent.change(inputs[0], { target: { value: "npm token" } });
    fireEvent.change(inputs[1], { target: { value: "~/.npmrc" } });
    fireEvent.change(inputs[2], { target: { value: "registry.npmjs.org:443" } });
    fireEvent.click(screen.getByText("Done"));
    await waitFor(() =>
      expect(setSandboxSettings).toHaveBeenLastCalledWith({
        credentials: [
          ...snapshot.credentials,
          { name: "npm-token", title: "npm token", path: "~/.npmrc", hosts: ["registry.npmjs.org:443"], does: undefined, enabled: true },
        ],
      }),
    );
    fireEvent.click(screen.getAllByText("Remove")[0]);
    await waitFor(() => {
      const calls = setSandboxSettings.mock.calls;
      const last = calls[calls.length - 1]?.[0];
      expect(last.credentials.map((c: any) => c.name)).toEqual(["gh", "npm-token"]);
    });
  });
});
