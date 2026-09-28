// Settings ▸ Sandbox (UX-051 A, UX-053 v3, OPE-207): one switch first; on reveals the type;
// a ready type reveals its options. The page shows what the machine reports and writes back
// the changes: provider, network profile, credential list, toolchain list. On Windows,
// choosing the sandbox opens the setup dialog, which calls the Windows setup route. Under
// OpenShell the readiness checklist and the guided setup job show.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const base = {
  platform: "darwin",
  provider: "",
  effective_provider: "direct",
  refused: "",
  providers: [
    { name: "direct", usable: true, why: "" },
    { name: "seatbelt", usable: true, why: "" },
    { name: "openshell", usable: false, why: "OpenShell is not installed" },
  ],
  windows_setup: null as any,
  network_profile: "strict",
  network_profiles: [
    { name: "strict", hosts: ["github.com"] },
    { name: "standard", hosts: ["github.com", "api.tavily.com"] },
    { name: "open", hosts: [] },
  ],
  credentials: [
    { name: "ssh", path: "~/.ssh", hosts: ["github.com:22"], label: "credential", enabled: false, kind: "folder", shipped: true },
    { name: "gh", path: "~/.config/gh", hosts: ["api.github.com:443"], label: "credential", enabled: true, kind: "", shipped: true },
    { name: "aws", path: "~/.aws/config", hosts: ["*.amazonaws.com:443"], label: "configuration", enabled: false, kind: "file", shipped: true },
  ],
  toolchains: [
    { name: "nvm", title: "nvm (Node versions)", path: "~/.nvm", enabled: true, exists: true, shipped: true },
    { name: "mytools", title: "My tools", path: "~/tools", enabled: true, exists: false, shipped: false },
  ],
  config_path: "/Users/sam/.config/coworker/config.toml",
};
let snapshot: any = { ...base };

const readiness = {
  platform: "linux",
  supported: true,
  all_ok: false,
  steps: [
    { key: "docker", what: "Docker is installed and this user can use it", ok: true, hint: "", fixable: false, command: "", docs: "" },
    // Handed over (no way to run as an administrator here): a command, and a guide.
    { key: "openshell", what: "OpenShell 0.0.116 is installed", ok: false, hint: "", fixable: false, command: "curl -LsSf https://example/install.sh | sh", docs: "https://example/guide" },
    { key: "gateway", what: "the gateway is running", ok: false, hint: "OpenShell is not installed", fixable: false, command: "", docs: "" },
    { key: "image", what: "the sandbox base image is downloaded (about 5 GB, one time)", ok: false, hint: "", fixable: true, command: "docker pull img", docs: "" },
  ],
};
let setupState: any = { status: "idle", rows: [], progress: null, error: "", elapsed_s: 0 };

// Like the backend: a provider change names the sessions it dropped for a rebuild.
const setSandboxSettings = vi.fn(async (patch: any) => ({ ok: true, ...snapshot, ...patch, ...("provider" in patch ? { rebuilt_sessions: ["s-open"] } : {}) }));
const onSandboxProviderChanged = vi.fn();
const startSandboxSetup = vi.fn(async () => setupState);
const runSandboxSetup = vi.fn(async () => ({ ok: true, checked: "the wall held", ...snapshot, provider: "windows", windows_setup: { ...snapshot.windows_setup, state: "ready", set_up_at: "2026-09-28T10:00:00Z" } }));
const runSandboxRemove = vi.fn(async () => ({ ok: true, ...snapshot, provider: "direct", windows_setup: { ...snapshot.windows_setup, state: "not_set_up", set_up_at: "" } }));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getSandboxSettings: vi.fn(async () => snapshot),
    setSandboxSettings: (patch: any) => setSandboxSettings(patch),
    runSandboxSetup: () => runSandboxSetup(),
    runSandboxRemove: () => runSandboxRemove(),
    getSandboxReadiness: vi.fn(async () => readiness),
    getSandboxSetup: vi.fn(async () => setupState),
    startSandboxSetup: () => startSandboxSetup(),
    cancelSandboxSetup: vi.fn(async () => setupState),
    getMachines: vi.fn(async () => ({ machines: [] })),
    getCloudMachines: vi.fn(async () => ({ machines: [] })),
    getCloudConnections: vi.fn(async () => []),
    getConnectors: vi.fn(async () => []),
    getCloudStatus: vi.fn(async () => ({ signed_in: false })),
    isCloudMode: () => false,
  };
});

import { SettingsView } from "./SettingsView";

const stripDisplay = (rows: any[]) => rows.map(({ kind: _k, shipped: _s, ...row }) => row);

describe("Settings ▸ Sandbox", () => {
  beforeEach(() => {
    snapshot = { ...base };
    setSandboxSettings.mockClear();
    runSandboxSetup.mockClear();
    runSandboxRemove.mockClear();
    startSandboxSetup.mockClear();
    onSandboxProviderChanged.mockClear();
    setupState = { status: "idle", rows: [], progress: null, error: "", elapsed_s: 0 };
  });
  afterEach(cleanup);

  it("off: one switch and nothing else; on reveals the type card only", async () => {
    render(<SettingsView initialTab="sandbox" onSandboxProviderChanged={onSandboxProviderChanged} />);
    await screen.findByTestId("sandbox-section");
    const sw = screen.getByRole("switch");
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("sandbox-switch-status").textContent).toBe("off");
    expect(screen.queryByTestId("sandbox-provider-seatbelt")).toBeNull();
    expect(screen.queryByTestId("sandbox-network-strict")).toBeNull();
    expect(screen.queryByTestId("sandbox-card-files")).toBeNull();
    fireEvent.click(sw);
    expect(screen.getByTestId("sandbox-switch-status").textContent).toBe("choose a type");
    expect((screen.getByTestId("sandbox-provider-openshell") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByTestId("sandbox-provider-openshell-why").textContent).toBe("OpenShell is not installed");
    expect(screen.queryByTestId("sandbox-network-strict")).toBeNull(); // no type is ready yet
    expect(setSandboxSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("sandbox-provider-seatbelt"));
    await waitFor(() => expect(setSandboxSettings).toHaveBeenCalledWith({ provider: "seatbelt" }));
    await waitFor(() => expect(onSandboxProviderChanged).toHaveBeenCalledWith(["s-open"])); // live sessions rebuilt under the new rule
  });

  it("OpenShell on Linux: the readiness checklist, its hints, the handover command and the setup button", async () => {
    snapshot = {
      ...base,
      platform: "linux",
      provider: "openshell",
      effective_provider: "",
      refused: "no session will start: OpenShell is not installed",
      providers: [
        { name: "direct", usable: true, why: "", state: "ready" },
        { name: "openshell", usable: false, why: "OpenShell is not installed", state: "unavailable" },
      ],
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-readiness");
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("sandbox-switch-status").textContent).toBe("on · OpenShell (NVIDIA)");
    expect((screen.getByTestId("sandbox-provider-openshell") as HTMLInputElement).checked).toBe(true);
    await screen.findByTestId("sandbox-readiness-row-openshell");
    expect(screen.getByText("3 requirements missing")).toBeTruthy();
    expect(screen.getByTestId("sandbox-readiness-row-docker").getAttribute("data-state")).toBe("ok");
    expect(screen.getByTestId("sandbox-readiness-row-openshell").getAttribute("data-state")).toBe("pending");
    expect(screen.getByText("curl -LsSf https://example/install.sh | sh")).toBeTruthy(); // the command to run
    // Only the handed-over row shows its command with Copy; the image row is the app's to
    // do, and a note (the gateway row) gets neither.
    expect(screen.getAllByText("Run this in a terminal on this machine:").length).toBe(1);
    expect(screen.getAllByText("Copy").length).toBe(1);
    expect(screen.queryByTestId("sandbox-readiness-command-gateway")).toBeNull();
    expect((screen.getByTestId("sandbox-readiness-docs-openshell") as HTMLAnchorElement).href).toBe("https://example/guide");
    expect((screen.getByTestId("sandbox-setup-start") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId("sandbox-card-tools")).toBeNull(); // OpenShell mounts no home folder: no tools card
    expect(screen.getByTestId("sandbox-card-files")).toBeTruthy();
  });

  it("OpenShell: a running setup job shows its rows and progress; needs_you offers Check again", async () => {
    snapshot = { ...base, platform: "linux", provider: "openshell", effective_provider: "openshell", providers: [{ name: "direct", usable: true, why: "", state: "ready" }, { name: "openshell", usable: true, why: "", state: "ready" }] };
    setupState = {
      status: "running",
      rows: [
        { ...readiness.steps[0], state: "ok" },
        { ...readiness.steps[1], ok: true, state: "ok" },
        { ...readiness.steps[2], ok: true, state: "ok" },
        { ...readiness.steps[3], state: "fixing" },
      ],
      progress: { layers_total: 8, layers_done: 3, last_line: "x: Downloading", elapsed_s: 75 },
      error: "",
      elapsed_s: 75,
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-download-progress");
    expect(screen.getByText("Downloading the base image: 3 of 8 layers, 1 min 15 s elapsed")).toBeTruthy();
    expect(screen.getByTestId("sandbox-setup-cancel")).toBeTruthy();
    expect(screen.getByTestId("sandbox-readiness-row-image").getAttribute("data-state")).toBe("fixing");
    cleanup();

    setupState = {
      status: "needs_you",
      rows: [
        { ...readiness.steps[0], state: "ok" },
        { ...readiness.steps[1], state: "needs_you" },
        { ...readiness.steps[2], state: "pending" },
        { ...readiness.steps[3], state: "pending" },
      ],
      progress: null,
      error: "",
      elapsed_s: 3,
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-setup-needs_you");
    expect(screen.getByText(/run the command shown in a terminal on this machine, then click Check again/)).toBeTruthy();
    expect(screen.getByTestId("sandbox-readiness-row-openshell").getAttribute("data-state")).toBe("needs_you");
    expect(screen.getByTestId("sandbox-setup-start").textContent).toBe("Check again");
    fireEvent.click(screen.getByTestId("sandbox-setup-start"));
    await waitFor(() => expect(startSandboxSetup).toHaveBeenCalled());
  });

  it("OpenShell with the base image missing is still choosable and says needs download", async () => {
    snapshot = {
      ...base,
      platform: "linux",
      providers: [
        { name: "direct", usable: true, why: "", state: "ready" },
        { name: "openshell", usable: false, why: "the base image is missing", state: "needs_download" },
      ],
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByRole("switch"));
    expect((screen.getByTestId("sandbox-provider-openshell") as HTMLInputElement).disabled).toBe(false);
    expect(screen.getByTestId("sandbox-provider-openshell-status").textContent).toBe("needs download");
    expect(screen.getByTestId("sandbox-provider-openshell-hint").textContent).toMatch(/about 5 GB/);
    expect(screen.queryByTestId("sandbox-provider-openshell-why")).toBeNull();
    fireEvent.click(screen.getByTestId("sandbox-provider-openshell"));
    await waitFor(() => expect(setSandboxSettings).toHaveBeenCalledWith({ provider: "openshell" }));
  });

  it("a ready type shows its options; switching off writes direct", async () => {
    snapshot = { ...base, provider: "seatbelt", effective_provider: "seatbelt" };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    expect(screen.getByTestId("sandbox-switch-status").textContent).toBe("on · macOS sandbox");
    expect((screen.getByTestId("sandbox-network-strict") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByTestId("sandbox-network-standard"));
    await waitFor(() => expect(setSandboxSettings).toHaveBeenCalledWith({ network_profile: "standard" }));
    // the files card is one line until opened; its chips name the entries
    expect(screen.getByTestId("sandbox-card-files")).toBeTruthy();
    expect(screen.queryByTestId("sandbox-card-files-body")).toBeNull();
    expect(within(screen.getByTestId("sandbox-card-files-toggle")).getByText("SSH keys")).toBeTruthy();
    expect(screen.getByTestId("sandbox-tools-summary").textContent).toContain("2 of 2 on");
    expect(screen.getByText(/config\.toml/)).toBeTruthy();
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(setSandboxSettings).toHaveBeenLastCalledWith({ provider: "direct" }));
  });

  it("the files card: switch, kind and label tags, add through the editor with a label, remove", async () => {
    snapshot = { ...base, provider: "seatbelt", effective_provider: "seatbelt" };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByTestId("sandbox-card-files-toggle"));
    const body = screen.getByTestId("sandbox-card-files-body");
    expect(screen.getByTestId("sandbox-credential-ssh-kind").textContent).toBe("folder");
    expect(screen.getByTestId("sandbox-credential-aws-kind").textContent).toBe("file");
    expect(within(screen.getByTestId("sandbox-credential-gh")).getByText("not on this machine")).toBeTruthy();
    expect(within(screen.getByTestId("sandbox-credential-aws")).getByText("configuration")).toBeTruthy();
    expect(within(body).getByText(/Also allows github.com:22/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("SSH keys"));
    await waitFor(() =>
      expect(setSandboxSettings).toHaveBeenLastCalledWith({
        credentials: stripDisplay([{ ...base.credentials[0], enabled: true }, base.credentials[1], base.credentials[2]]),
      }),
    );
    fireEvent.click(screen.getByTestId("sandbox-credential-add"));
    const editor = screen.getByTestId("sandbox-credential-editor");
    const inputs = editor.querySelectorAll("input[type=text], input:not([type]), textarea");
    fireEvent.change(inputs[0], { target: { value: "npm token" } });
    fireEvent.change(inputs[1], { target: { value: "~/.npmrc" } });
    fireEvent.click(screen.getByTestId("sandbox-credential-label-configuration"));
    fireEvent.change(inputs[2], { target: { value: "registry.npmjs.org:443" } });
    fireEvent.click(within(editor).getByText("Done"));
    await waitFor(() =>
      expect(setSandboxSettings).toHaveBeenLastCalledWith({
        credentials: [
          ...stripDisplay([{ ...base.credentials[0], enabled: true }, base.credentials[1], base.credentials[2]]),
          { name: "npm-token", title: "npm token", path: "~/.npmrc", hosts: ["registry.npmjs.org:443"], does: undefined, label: "configuration", enabled: true },
        ],
      }),
    );
    fireEvent.click(within(screen.getByTestId("sandbox-credential-ssh")).getByText("Remove"));
    await waitFor(() => {
      const calls = setSandboxSettings.mock.calls;
      const last = calls[calls.length - 1]?.[0];
      expect(last.credentials.map((c: any) => c.name)).toEqual(["gh", "aws", "npm-token"]);
    });
  });

  it("the tools card: switch a folder off and add one, without the display-only fields", async () => {
    snapshot = { ...base, provider: "seatbelt", effective_provider: "seatbelt" };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByTestId("sandbox-card-tools-toggle"));
    expect(screen.getByText(/not on this machine/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("nvm (Node versions)"));
    await waitFor(() =>
      expect(setSandboxSettings).toHaveBeenLastCalledWith({
        toolchains: [
          { name: "nvm", title: "nvm (Node versions)", path: "~/.nvm", enabled: false },
          { name: "mytools", title: "My tools", path: "~/tools", enabled: true },
        ],
      }),
    );
    fireEvent.click(screen.getByTestId("sandbox-toolchain-add"));
    const editor = screen.getByTestId("sandbox-toolchain-editor");
    const inputs = editor.querySelectorAll("input");
    fireEvent.change(inputs[0], { target: { value: "JDKs" } });
    fireEvent.change(inputs[1], { target: { value: "~/.jdks" } });
    fireEvent.click(within(editor).getByText("Done"));
    await waitFor(() => {
      const calls = setSandboxSettings.mock.calls;
      const last = calls[calls.length - 1]?.[0];
      expect(last.toolchains[last.toolchains.length - 1]).toEqual({ name: "jdks", title: "JDKs", path: "~/.jdks", enabled: true });
    });
  });

  it("Windows: choosing the sandbox opens the setup dialog; Set up now calls the route; Done shows the options", async () => {
    snapshot = {
      ...base,
      platform: "win32",
      providers: [
        { name: "direct", usable: true, why: "" },
        { name: "windows", usable: false, why: "the one-time setup has not run on this PC" },
        { name: "openshell", usable: false, why: "OpenShell is not available on Windows" },
      ],
      windows_setup: { state: "not_set_up", set_up_at: "", problem: "not run", can_elevate: true, command: "openworker machine sandbox setup" },
      network_profile: "open",
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.getByText("one-time setup")).toBeTruthy();
    expect(screen.queryByTestId("sandbox-provider-windows-why")).toBeNull(); // an administrator sees no warning
    fireEvent.click(screen.getByTestId("sandbox-provider-windows"));
    const dialog = screen.getByTestId("sandbox-setup-dialog");
    expect(within(dialog).getByText("Set up the Windows sandbox")).toBeTruthy();
    expect(setSandboxSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("sandbox-setup-now"));
    await waitFor(() => expect(runSandboxSetup).toHaveBeenCalled());
    await screen.findByTestId("sandbox-setup-done-box");
    expect(screen.getByTestId("sandbox-setup-done-box").textContent).toContain("the wall held");
    fireEvent.click(screen.getByTestId("sandbox-setup-done"));
    expect(screen.queryByTestId("sandbox-setup-dialog")).toBeNull();
    expect(screen.getByTestId("sandbox-switch-status").textContent).toBe("on · Windows sandbox");
    expect(screen.getByTestId("sandbox-windows-setup-line").textContent).toContain("Set up on");
    // Open is first and chosen on Windows; the options are visible now
    const radios = document.querySelectorAll('input[name="sandbox-network"]');
    expect(radios[0].getAttribute("data-testid")).toBe("sandbox-network-open");
    expect((screen.getByTestId("sandbox-network-open") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("sandbox-card-files")).toBeTruthy();
    expect(screen.getByTestId("sandbox-card-tools")).toBeTruthy();
    // Remove setup: confirm, the route runs, the page collapses
    fireEvent.click(screen.getByTestId("sandbox-remove-setup"));
    fireEvent.click(screen.getByTestId("sandbox-remove-confirm"));
    await waitFor(() => expect(runSandboxRemove).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId("sandbox-switch-status").textContent).toBe("off"));
    expect(screen.queryByTestId("sandbox-card-files")).toBeNull();
  });

  it("Windows, not an administrator: the row is disabled with the command; Not now turns the switch off", async () => {
    snapshot = {
      ...base,
      platform: "win32",
      providers: [
        { name: "direct", usable: true, why: "" },
        { name: "windows", usable: false, why: "the one-time setup has not run on this PC" },
        { name: "openshell", usable: false, why: "OpenShell is not available on Windows" },
      ],
      windows_setup: { state: "not_set_up", set_up_at: "", problem: "not run", can_elevate: false, command: "openworker machine sandbox setup" },
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.getByTestId("sandbox-switch-status").textContent).toBe("no type can be used yet");
    expect((screen.getByTestId("sandbox-provider-windows") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByTestId("sandbox-provider-windows-why").textContent).toContain("openworker machine sandbox setup");
    expect(screen.getByText("needs an administrator")).toBeTruthy();
    // an administrator, but "Not now"
    snapshot = { ...snapshot, windows_setup: { ...snapshot.windows_setup, can_elevate: true } };
    cleanup();
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByRole("switch"));
    fireEvent.click(screen.getByTestId("sandbox-provider-windows"));
    fireEvent.click(screen.getByTestId("sandbox-setup-not-now"));
    expect(screen.queryByTestId("sandbox-setup-dialog")).toBeNull();
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");
    expect(runSandboxSetup).not.toHaveBeenCalled();
  });
});
