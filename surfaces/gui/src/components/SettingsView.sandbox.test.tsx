// Settings ▸ Sandbox (UX-051 A, UX-053 v5 and v6, OPE-207): one switch first; on reveals the type;
// a chosen type reveals its options. The page shows what the machine reports and writes back
// the changes: provider, network profile and the machine's ticked sites, the credential list,
// the toolchain list. A type that is not set up looks disabled with one "Set up" button: on
// Windows it opens the setup dialog, which calls the Windows setup route; for OpenShell it
// opens three rows: Docker running, OpenShell installed, and the app's own setup job.
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
  network_profile: "allowlist",
  network_profiles: [{ name: "allowlist" }, { name: "open" }],
  network_sites: [
    { group: "code-hosts", hosts: ["github.com:443", "gitlab.com:443"] },
    { group: "package-registries", hosts: ["pypi.org:443", "registry.npmjs.org:443"] },
    { group: "search-apis", hosts: ["api.tavily.com:443"] },
  ],
  network_hosts: [] as string[],
  credentials: [
    { name: "ssh", path: "~/.ssh", hosts: ["github.com:22"], label: "credential", enabled: true, kind: "folder", shipped: true },
    { name: "aws", path: "~/.aws/config", hosts: ["*.amazonaws.com:443"], label: "configuration", enabled: true, kind: "file", shipped: true },
    { name: "gh", path: "~/.config/gh", hosts: ["api.github.com:443"], label: "credential", enabled: false, kind: "", shipped: true },
  ],
  credential_presets: [
    { name: "npm", path: "~/.npmrc", hosts: ["registry.npmjs.org:443"], label: "credential", enabled: false, kind: "file", shipped: true },
    { name: "gcloud", path: "~/.config/gcloud", hosts: ["*.googleapis.com:443"], label: "credential", enabled: false, kind: "", shipped: true },
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
let readinessNow: any = readiness;
let setupState: any = { status: "idle", rows: [], progress: null, error: "", elapsed_s: 0 };

// Like the backend: a provider change names the sessions it dropped for a rebuild.
const setSandboxSettings = vi.fn(async (patch: any) => ({ ok: true, ...snapshot, ...patch, ...("provider" in patch ? { rebuilt_sessions: ["s-open"] } : {}) }));
const onSandboxProviderChanged = vi.fn();
const startSandboxSetup = vi.fn(async () => setupState);
const runSandboxSetup = vi.fn(async () => ({ ok: true, checked: "the wall held", ...snapshot, provider: "windows", providers: snapshot.providers.map((p: any) => (p.name === "windows" ? { ...p, usable: true, why: "" } : p)), windows_setup: { ...snapshot.windows_setup, state: "ready", set_up_at: "2026-09-28T10:00:00Z" } }));
const runSandboxRemove = vi.fn(async () => ({ ok: true, ...snapshot, provider: "direct", windows_setup: { ...snapshot.windows_setup, state: "not_set_up", set_up_at: "" } }));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getSandboxSettings: vi.fn(async () => snapshot),
    setSandboxSettings: (patch: any) => setSandboxSettings(patch),
    runSandboxSetup: () => runSandboxSetup(),
    runSandboxRemove: () => runSandboxRemove(),
    getSandboxReadiness: vi.fn(async () => readinessNow),
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
import { cleanHost } from "./SandboxSection";

const stripDisplay = (rows: any[]) => rows.map(({ kind: _k, shipped: _s, ...row }) => row);
const lastPatch = () => {
  const calls = setSandboxSettings.mock.calls;
  return calls[calls.length - 1]?.[0];
};
const masterSwitch = () => within(screen.getByTestId("sandbox-section")).getAllByRole("switch")[0];
const chosenSeatbelt = () => ({ ...base, provider: "seatbelt", effective_provider: "seatbelt" });

describe("Settings ▸ Sandbox", () => {
  beforeEach(() => {
    snapshot = { ...base };
    setSandboxSettings.mockClear();
    runSandboxSetup.mockClear();
    runSandboxRemove.mockClear();
    startSandboxSetup.mockClear();
    onSandboxProviderChanged.mockClear();
    setupState = { status: "idle", rows: [], progress: null, error: "", elapsed_s: 0 };
    readinessNow = readiness;
  });
  afterEach(cleanup);

  it("off: one switch and one line; on reveals the types, OpenShell disabled with one Set up button", async () => {
    render(<SettingsView initialTab="sandbox" onSandboxProviderChanged={onSandboxProviderChanged} />);
    await screen.findByTestId("sandbox-section");
    expect(screen.getByText("Manage environment restrictions for your Agent.")).toBeTruthy();
    expect(screen.getByText("Agents can only use the folders you open. The rest of your computer stays private.")).toBeTruthy();
    expect(masterSwitch().getAttribute("aria-checked")).toBe("false");
    expect(screen.queryByTestId("sandbox-switch-status")).toBeNull(); // the switch is its own cue
    expect(screen.queryByTestId("sandbox-provider-seatbelt")).toBeNull();
    expect(screen.queryByTestId("sandbox-card-files")).toBeNull();
    fireEvent.click(masterSwitch());
    expect(screen.getByText("Built into macOS.")).toBeTruthy();
    expect((screen.getByTestId("sandbox-provider-openshell") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByTestId("sandbox-setup-openshell").textContent).toBe("Set up");
    expect(screen.queryByTestId("sandbox-provider-openshell-why")).toBeNull(); // the button says it all
    expect(screen.queryByTestId("sandbox-network-allowlist")).toBeNull(); // no type is chosen yet
    expect(setSandboxSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("sandbox-provider-seatbelt"));
    await waitFor(() => expect(setSandboxSettings).toHaveBeenCalledWith({ provider: "seatbelt" }));
    await waitFor(() => expect(onSandboxProviderChanged).toHaveBeenCalledWith(["s-open"])); // live sessions rebuilt under the new rule
  });

  it("OpenShell's Set up: three rows; a failed check says what to do; the app never installs it", async () => {
    snapshot = {
      ...base,
      platform: "linux",
      providers: [
        { name: "direct", usable: true, why: "", state: "ready" },
        { name: "openshell", usable: false, why: "OpenShell is not installed", state: "unavailable" },
      ],
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(masterSwitch());
    expect(screen.getByText("One Linux container per agent. Needs Docker.")).toBeTruthy();
    fireEvent.click(screen.getByTestId("sandbox-setup-openshell"));
    const dialog = await screen.findByTestId("sandbox-openshell-dialog");
    await within(dialog).findByTestId("sandbox-setup-row-openshell");
    expect(within(dialog).getByText("Set up OpenShell")).toBeTruthy();
    expect(dialog.querySelectorAll("li").length).toBe(3); // the server's finer steps fold into the third row
    expect(screen.getByTestId("sandbox-setup-row-docker").getAttribute("data-state")).toBe("ok");
    expect(screen.getByTestId("sandbox-setup-row-openshell").getAttribute("data-state")).toBe("bad");
    expect(within(screen.getByTestId("sandbox-setup-row-openshell")).getByText(/Install OpenShell 0\.0\.116, then check again/)).toBeTruthy();
    expect((screen.getByTestId("sandbox-setup-docs-openshell") as HTMLAnchorElement).href).toBe("https://example/guide");
    expect(screen.getByTestId("sandbox-setup-row-setup").getAttribute("data-state")).toBe("wait");
    expect(within(dialog).queryByText(/curl/)).toBeNull(); // no installer command
    expect(within(dialog).queryByText("to do")).toBeNull();
    expect(screen.queryByTestId("sandbox-setup-start")).toBeNull(); // a check failed: Check again, not Set up
    fireEvent.click(screen.getByTestId("sandbox-setup-check"));
    fireEvent.click(screen.getByTestId("sandbox-openshell-close"));
    expect(screen.queryByTestId("sandbox-openshell-dialog")).toBeNull();
  });

  it("OpenShell on a Mac whose Docker Desktop kernel lacks Landlock: update Docker Desktop, then the reason", async () => {
    snapshot = { ...base, platform: "darwin", providers: [{ name: "direct", usable: true, why: "" }, { name: "seatbelt", usable: true, why: "" }, { name: "openshell", usable: false, why: "", state: "unavailable" }] };
    readinessNow = {
      ...readiness,
      steps: [
        { ...readiness.steps[0], ok: true },
        { key: "docker_landlock", what: "Docker Desktop's Linux kernel supports Landlock", ok: false, hint: "Update Docker Desktop", fixable: false, command: "", docs: "https://docs.docker.com/desktop/setup/install/mac-install/" },
        { ...readiness.steps[1], ok: true },
        { ...readiness.steps[2], ok: true },
        readiness.steps[3],
      ],
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(masterSwitch());
    fireEvent.click(screen.getByTestId("sandbox-setup-openshell"));
    await screen.findByTestId("sandbox-setup-landlock");
    const dialog = screen.getByTestId("sandbox-openshell-dialog");
    expect(dialog.querySelectorAll("li").length).toBe(3); // still three rows: the kernel folds into Docker's
    expect(screen.getByTestId("sandbox-setup-row-docker").getAttribute("data-state")).toBe("bad");
    const lines = screen.getByTestId("sandbox-setup-landlock").children;
    expect(lines[0].textContent).toBe("Update Docker Desktop, then check again. Get Docker Desktop");
    expect(lines[1].textContent).toBe("This version's Linux kernel has no Landlock, which OpenShell needs.");
    expect((screen.getByTestId("sandbox-setup-docs-landlock") as HTMLAnchorElement).href).toBe("https://docs.docker.com/desktop/setup/install/mac-install/");
    expect(screen.queryByTestId("sandbox-setup-start")).toBeNull(); // Check again, not Set up
    expect(screen.getByTestId("sandbox-setup-check")).toBeTruthy();
  });

  it("OpenShell on a Mac with Docker Desktop's host networking off: where to turn it on, then check again", async () => {
    snapshot = { ...base, platform: "darwin", providers: [{ name: "direct", usable: true, why: "" }, { name: "seatbelt", usable: true, why: "" }, { name: "openshell", usable: false, why: "", state: "unavailable" }] };
    readinessNow = {
      ...readiness,
      steps: [
        { ...readiness.steps[0], ok: true },
        { key: "docker_host_network", what: "Docker Desktop's host networking is on", ok: false, hint: "Turn on host networking in Docker Desktop", fixable: false, command: "", docs: "https://docs.docker.com/engine/network/drivers/host/#docker-desktop" },
        { ...readiness.steps[1], ok: true },
        { ...readiness.steps[2], ok: true },
        readiness.steps[3],
      ],
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(masterSwitch());
    fireEvent.click(screen.getByTestId("sandbox-setup-openshell"));
    await screen.findByTestId("sandbox-setup-hostnet");
    const dialog = screen.getByTestId("sandbox-openshell-dialog");
    expect(dialog.querySelectorAll("li").length).toBe(3); // still three rows: it folds into Docker's
    expect(screen.getByTestId("sandbox-setup-row-docker").getAttribute("data-state")).toBe("bad");
    const lines = screen.getByTestId("sandbox-setup-hostnet").children;
    expect(lines[0].textContent).toBe("Turn on host networking in Docker Desktop, then check again. Docker's guide");
    expect(lines[1].textContent).toBe("Settings ▸ Resources ▸ Network ▸ Enable host networking, then Apply & restart.");
    expect(lines[2].textContent).toBe("OpenShell's sandboxes reach its gateway that way.");
    expect((screen.getByTestId("sandbox-setup-docs-hostnet") as HTMLAnchorElement).href).toBe("https://docs.docker.com/engine/network/drivers/host/#docker-desktop");
    expect(screen.queryByTestId("sandbox-setup-start")).toBeNull(); // Check again, not Set up
    expect(screen.getByTestId("sandbox-setup-check")).toBeTruthy();
  });

  it("OpenShell: both checks pass, Set up runs; the rows stay and the third shows the download", async () => {
    snapshot = { ...base, platform: "darwin", providers: [{ name: "direct", usable: true, why: "" }, { name: "seatbelt", usable: true, why: "" }, { name: "openshell", usable: false, why: "", state: "needs_download" }] };
    readinessNow = { ...readiness, steps: [{ ...readiness.steps[0], ok: true }, { ...readiness.steps[1], ok: true }, { ...readiness.steps[2], ok: true }, readiness.steps[3]] };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(masterSwitch());
    fireEvent.click(screen.getByTestId("sandbox-setup-openshell"));
    await screen.findByTestId("sandbox-setup-row-setup");
    expect(screen.getByText("Docker Desktop is running")).toBeTruthy();
    expect(screen.getByTestId("sandbox-setup-row-setup").getAttribute("data-state")).toBe("todo");
    expect(screen.getByText("Downloads the base image, about 5 GB, once.")).toBeTruthy();
    fireEvent.click(screen.getByTestId("sandbox-setup-start"));
    await waitFor(() => expect(startSandboxSetup).toHaveBeenCalled());
    cleanup();

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
    await screen.findByTestId("sandbox-section");
    fireEvent.click(masterSwitch());
    await waitFor(() => expect(screen.getByTestId("sandbox-setup-openshell").textContent).toBe("Setting up…")); // the job was adopted
    fireEvent.click(screen.getByTestId("sandbox-setup-openshell"));
    await screen.findByTestId("sandbox-download-progress");
    expect(screen.getByText("Setting up OpenShell")).toBeTruthy();
    expect(screen.getByTestId("sandbox-setup-row-docker").getAttribute("data-state")).toBe("ok");
    expect(screen.getByTestId("sandbox-setup-row-setup").getAttribute("data-state")).toBe("run");
    expect(screen.getByText("Downloading the base image: 3 of 8 layers, 1 min 15 s elapsed")).toBeTruthy();
    expect(screen.getByTestId("sandbox-setup-cancel").textContent).toBe("Cancel setup");
    expect(screen.getByTestId("sandbox-openshell-hide").textContent).toBe("Hide");
    cleanup();

    setupState = { ...setupState, status: "done", progress: null, rows: setupState.rows.map((r: any) => ({ ...r, ok: true, state: r.state === "fixing" ? "fixed" : r.state })) };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(masterSwitch());
    await waitFor(() => expect(screen.getByTestId("sandbox-setup-openshell")).toBeTruthy());
    fireEvent.click(screen.getByTestId("sandbox-setup-openshell"));
    await screen.findByText("OpenShell is ready");
    expect(screen.getByTestId("sandbox-setup-row-setup").getAttribute("data-state")).toBe("ok");
    expect(screen.getByTestId("sandbox-openshell-done")).toBeTruthy();
  });

  it("OpenShell chosen with the base image missing: selected, with the hint and the Set up button", async () => {
    snapshot = {
      ...base,
      platform: "linux",
      provider: "openshell",
      effective_provider: "",
      providers: [
        { name: "direct", usable: true, why: "", state: "ready" },
        { name: "openshell", usable: false, why: "the base image is missing", state: "needs_download" },
      ],
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    expect((screen.getByTestId("sandbox-provider-openshell") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("sandbox-provider-openshell-hint").textContent).toMatch(/about 5 GB/);
    expect(screen.getByTestId("sandbox-setup-openshell")).toBeTruthy();
    expect(screen.queryByTestId("sandbox-card-tools")).toBeNull(); // OpenShell mounts no home folder: no tools panel
    expect(screen.getByTestId("sandbox-card-files")).toBeTruthy();
  });

  it("OpenShell chosen: only the allow list is offered, no Allow everything", async () => {
    snapshot = {
      ...base,
      platform: "linux",
      provider: "openshell",
      effective_provider: "openshell",
      providers: [
        { name: "direct", usable: true, why: "", state: "ready" },
        { name: "openshell", usable: true, why: "", state: "ready" },
      ],
      network_profiles: [{ name: "allowlist" }], // what the server sends with OpenShell
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    expect(document.querySelectorAll('input[name="sandbox-network"]').length).toBe(1);
    expect((screen.getByTestId("sandbox-network-allowlist") as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByTestId("sandbox-network-open")).toBeNull();
    expect(screen.queryByText("Allow everything")).toBeNull();
  });

  it("a chosen type shows two network choices and two closed panels; off writes direct", async () => {
    snapshot = chosenSeatbelt();
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    expect(document.querySelectorAll('input[name="sandbox-network"]').length).toBe(2);
    expect((screen.getByTestId("sandbox-network-allowlist") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("Only the sites you allow")).toBeTruthy();
    expect(screen.getByTestId("sandbox-network-allowlist-desc").textContent).toBe("None yet.Choose sites…");
    expect(screen.getByText("Allow everything").className).toContain("text-warnInk");
    fireEvent.click(screen.getByTestId("sandbox-network-open"));
    await waitFor(() => expect(setSandboxSettings).toHaveBeenCalledWith({ network_profile: "open" }));
    expect(screen.queryByTestId("sandbox-card-files-body")).toBeNull();
    expect(screen.getByTestId("sandbox-card-files-summary").textContent).toBe("SSH keys and AWS profiles. Copies are deleted when the session ends.");
    expect(screen.queryByTestId("sandbox-card-tools-body")).toBeNull();
    expect(screen.getByTestId("sandbox-card-tools-summary").textContent).toBe("nvm (Node versions) and My tools. Agents can run them, not change them.");
    expect(screen.getByText(/config\.toml/)).toBeTruthy();
    fireEvent.click(masterSwitch());
    await waitFor(() => expect(setSandboxSettings).toHaveBeenLastCalledWith({ provider: "direct" }));
  });

  it("Allowed sites: nothing ticked; tick a group and a site, add one, refuse a bad one, save the ticked", async () => {
    snapshot = { ...chosenSeatbelt(), network_hosts: ["sentry.io:443"] };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    expect(screen.getByTestId("sandbox-network-allowlist-desc").textContent).toBe("sentry.io.Choose sites…");
    fireEvent.click(screen.getByTestId("sandbox-network-sites"));
    const dialog = screen.getByTestId("sandbox-sites-dialog");
    expect(within(dialog).getByText("Allowed sites")).toBeTruthy();
    expect(within(dialog).getByText("Agents can reach only the sites you tick. Applies to new sessions.")).toBeTruthy();
    expect(within(screen.getByTestId("sandbox-sites-group-added")).getByText("sentry.io")).toBeTruthy(); // a site the user added earlier
    const box = (host: string) => within(screen.getByTestId(`sandbox-site-${host}`)).getByRole("checkbox") as HTMLInputElement;
    expect(box("github.com:443").checked).toBe(false); // nothing from the catalogue is ticked
    fireEvent.click(screen.getByTestId("sandbox-sites-group-code-hosts-box"));
    expect(box("github.com:443").checked && box("gitlab.com:443").checked).toBe(true);
    fireEvent.click(box("pypi.org:443"));
    expect((screen.getByTestId("sandbox-sites-group-package-registries-box") as HTMLInputElement).indeterminate).toBe(true);
    const input = screen.getByTestId("sandbox-site-input");
    fireEvent.change(input, { target: { value: "not a site" } });
    fireEvent.click(screen.getByTestId("sandbox-site-add"));
    expect(within(dialog).getByText(/That is not a site name/)).toBeTruthy();
    fireEvent.change(input, { target: { value: "Registry.Acme.dev" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(box("registry.acme.dev:443").checked).toBe(true);
    fireEvent.click(within(screen.getByTestId("sandbox-site-sentry.io:443")).getByText("Remove"));
    expect(screen.getByTestId("sandbox-sites-total").textContent).toBe("4 allowed");
    fireEvent.click(screen.getByTestId("sandbox-sites-save"));
    await waitFor(() => expect(setSandboxSettings).toHaveBeenLastCalledWith({ network_hosts: ["github.com:443", "gitlab.com:443", "pypi.org:443", "registry.acme.dev:443"] }));
    await waitFor(() => expect(screen.queryByTestId("sandbox-sites-dialog")).toBeNull());
  });

  it("the files panel lists only added entries: switch, tags, remove; Add… offers a CLI's login or a file", async () => {
    snapshot = chosenSeatbelt();
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByTestId("sandbox-card-files-toggle"));
    const body = screen.getByTestId("sandbox-card-files-body");
    expect(screen.getByTestId("sandbox-credential-ssh-kind").textContent).toBe("folder");
    expect(screen.getByTestId("sandbox-credential-aws-kind").textContent).toBe("file");
    expect(within(screen.getByTestId("sandbox-credential-gh")).getByText("not on this machine")).toBeTruthy();
    expect(within(screen.getByTestId("sandbox-credential-aws")).getByText("configuration")).toBeTruthy();
    expect(within(body).getByText(/Also allows github.com:22/)).toBeTruthy();
    expect(within(body).getByText("Logins kept in the macOS Keychain are not files and cannot be added.")).toBeTruthy();
    expect(screen.queryByTestId("sandbox-credential-npm")).toBeNull(); // a preset is not listed until added
    fireEvent.click(within(screen.getByTestId("sandbox-credential-ssh")).getByRole("switch"));
    await waitFor(() => expect(lastPatch()).toEqual({ credentials: stripDisplay([{ ...base.credentials[0], enabled: false }, base.credentials[1], base.credentials[2]]) }));
    fireEvent.click(screen.getByTestId("sandbox-credential-gh-remove"));
    await waitFor(() => expect(lastPatch().credentials.map((c: any) => c.name)).toEqual(["ssh", "aws"]));

    // A CLI's login: found presets can be added, missing ones say so
    fireEvent.click(screen.getByTestId("sandbox-credential-add"));
    fireEvent.click(screen.getByTestId("sandbox-add-cli"));
    const picker = screen.getByTestId("sandbox-cli-picker");
    expect(screen.getByTestId("sandbox-preset-npm").getAttribute("data-found")).toBe("yes");
    expect(within(screen.getByTestId("sandbox-preset-npm")).getByText("~/.npmrc · Install and publish private packages")).toBeTruthy();
    expect(within(screen.getByTestId("sandbox-preset-gcloud")).getByText("not found")).toBeTruthy();
    expect(screen.getByTestId("sandbox-badge-npm").querySelector("path")?.getAttribute("fill") ?? screen.getByTestId("sandbox-badge-npm").querySelector("svg")?.getAttribute("fill")).toBe("#CB3837"); // the real npm mark
    expect(within(screen.getByTestId("sandbox-preset-gcloud")).getByText("not on this Mac")).toBeTruthy();
    fireEvent.click(screen.getByTestId("sandbox-preset-npm-add"));
    await waitFor(() => expect(lastPatch().credentials[lastPatch().credentials.length - 1]).toEqual({ name: "npm", enabled: true }));
    fireEvent.click(within(picker).getByTestId("sandbox-cli-done"));
    expect(screen.queryByTestId("sandbox-cli-picker")).toBeNull();

    // A file or folder: the modal, with a label
    fireEvent.click(screen.getByTestId("sandbox-credential-add"));
    fireEvent.click(screen.getByTestId("sandbox-add-file"));
    const editor = screen.getByTestId("sandbox-credential-editor");
    expect(within(editor).getByText("Add a file or folder")).toBeTruthy();
    fireEvent.change(screen.getByTestId("sandbox-credential-path"), { target: { value: "~/.config/acme/token" } });
    fireEvent.change(screen.getByTestId("sandbox-credential-title"), { target: { value: "Acme CLI token" } });
    fireEvent.click(screen.getByTestId("sandbox-credential-label-configuration"));
    fireEvent.change(editor.querySelector("textarea")!, { target: { value: "api.acme.dev" } });
    fireEvent.click(screen.getByTestId("sandbox-credential-save"));
    await waitFor(() =>
      expect(lastPatch().credentials[lastPatch().credentials.length - 1]).toEqual({
        name: "acme-cli-token",
        title: "Acme CLI token",
        path: "~/.config/acme/token",
        hosts: ["api.acme.dev:443"],
        does: undefined,
        label: "configuration",
        enabled: true,
      }),
    );
  });

  it("nothing added yet: the panel says so in one line and offers Add…", async () => {
    snapshot = { ...chosenSeatbelt(), credentials: [] };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    expect(screen.getByTestId("sandbox-card-files-summary").textContent).toBe("Nothing is copied into sandboxes.");
    fireEvent.click(screen.getByTestId("sandbox-card-files-toggle"));
    expect(screen.getByTestId("sandbox-files-empty").textContent).toContain("Add a CLI's login");
    expect(screen.getByTestId("sandbox-credential-add").textContent).toBe("Add…");
  });

  it("the tools panel: switch a folder off and add one, without the display-only fields", async () => {
    snapshot = chosenSeatbelt();
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(screen.getByTestId("sandbox-card-tools-toggle"));
    expect(screen.getByText("Tools shown to Agent with read-only access")).toBeTruthy();
    expect(within(screen.getByTestId("sandbox-toolchain-mytools")).getByText(/not on this machine/)).toBeTruthy();
    fireEvent.click(within(screen.getByTestId("sandbox-toolchain-nvm")).getByRole("switch"));
    await waitFor(() =>
      expect(lastPatch()).toEqual({
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
    fireEvent.click(within(editor).getByText("Add"));
    await waitFor(() => expect(lastPatch().toolchains[lastPatch().toolchains.length - 1]).toEqual({ name: "jdks", title: "JDKs", path: "~/.jdks", enabled: true }));
  });

  it("Windows: Set up opens the setup dialog; Set up now calls the route; Done shows the options", async () => {
    snapshot = {
      ...base,
      platform: "win32",
      providers: [
        { name: "direct", usable: true, why: "" },
        { name: "windows", usable: false, why: "the one-time setup has not run on this PC" },
        { name: "openshell", usable: false, why: "OpenShell is not available on Windows yet." },
      ],
      windows_setup: { state: "not_set_up", set_up_at: "", problem: "not run", can_elevate: true, command: "openworker machine sandbox setup" },
      network_profile: "open",
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(masterSwitch());
    expect(screen.getByText("Built into Windows.")).toBeTruthy();
    expect((screen.getByTestId("sandbox-provider-windows") as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByTestId("sandbox-provider-windows-why")).toBeNull(); // an administrator sees no warning
    expect(screen.queryByTestId("sandbox-setup-openshell")).toBeNull(); // nothing to set up for OpenShell on Windows
    expect(screen.getByTestId("sandbox-provider-openshell-why").textContent).toBe("OpenShell is not available on Windows yet.");
    fireEvent.click(screen.getByTestId("sandbox-setup-windows"));
    const dialog = screen.getByTestId("sandbox-setup-dialog");
    expect(within(dialog).getByText("Set up the Windows sandbox")).toBeTruthy();
    expect(setSandboxSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("sandbox-setup-now"));
    await waitFor(() => expect(runSandboxSetup).toHaveBeenCalled());
    await screen.findByTestId("sandbox-setup-done-box");
    expect(screen.getByTestId("sandbox-setup-done-box").textContent).toContain("the wall held");
    fireEvent.click(screen.getByTestId("sandbox-setup-done"));
    expect(screen.queryByTestId("sandbox-setup-dialog")).toBeNull();
    expect((screen.getByTestId("sandbox-provider-windows") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("sandbox-windows-setup-line").textContent).toContain("Set up on");
    expect((screen.getByTestId("sandbox-network-open") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("sandbox-card-files")).toBeTruthy();
    expect(screen.getByTestId("sandbox-card-tools")).toBeTruthy();
    fireEvent.click(screen.getByTestId("sandbox-card-files-toggle"));
    expect(screen.getByText("Logins kept in Windows Credential Manager are not files and cannot be added.")).toBeTruthy();
    // Remove setup: confirm, the route runs, the page collapses
    fireEvent.click(screen.getByTestId("sandbox-remove-setup"));
    fireEvent.click(screen.getByTestId("sandbox-remove-confirm"));
    await waitFor(() => expect(runSandboxRemove).toHaveBeenCalled());
    await waitFor(() => expect(masterSwitch().getAttribute("aria-checked")).toBe("false"));
    expect(screen.queryByTestId("sandbox-card-files")).toBeNull();
  });

  it("Windows, not an administrator: the row is disabled with the command and no button; Not now turns the switch off", async () => {
    snapshot = {
      ...base,
      platform: "win32",
      providers: [
        { name: "direct", usable: true, why: "" },
        { name: "windows", usable: false, why: "the one-time setup has not run on this PC" },
        { name: "openshell", usable: false, why: "OpenShell is not available on Windows yet." },
      ],
      windows_setup: { state: "not_set_up", set_up_at: "", problem: "not run", can_elevate: false, command: "openworker machine sandbox setup" },
    };
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(masterSwitch());
    expect((screen.getByTestId("sandbox-provider-windows") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByTestId("sandbox-provider-windows-why").textContent).toContain("openworker machine sandbox setup");
    expect(screen.queryByTestId("sandbox-setup-windows")).toBeNull();
    // an administrator, but "Not now"
    snapshot = { ...snapshot, windows_setup: { ...snapshot.windows_setup, can_elevate: true } };
    cleanup();
    render(<SettingsView initialTab="sandbox" />);
    await screen.findByTestId("sandbox-section");
    fireEvent.click(masterSwitch());
    fireEvent.click(screen.getByTestId("sandbox-setup-windows"));
    fireEvent.click(screen.getByTestId("sandbox-setup-not-now"));
    expect(screen.queryByTestId("sandbox-setup-dialog")).toBeNull();
    expect(masterSwitch().getAttribute("aria-checked")).toBe("false");
    expect(runSandboxSetup).not.toHaveBeenCalled();
  });

  it("cleanHost follows the server's rule", () => {
    expect(cleanHost("Registry.Acme.dev")).toBe("registry.acme.dev:443");
    expect(cleanHost("https://api.acme.dev/v1")).toBe("api.acme.dev:443");
    expect(cleanHost("*.acme.dev:8443")).toBe("*.acme.dev:8443");
    for (const bad of ["", "localhost", "acme dev", "api.acme.dev:0", "a..b"]) expect(cleanHost(bad)).toBe("");
  });
});
