// #43: Settings ▸ Models must not sit on "Loading…" forever. A settings fetch that fails
// (or is aborted by the 10 s timeout) shows one error line with Retry; Retry fetches again.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ModelSettings } from "../api";

vi.mock("../tauri", () => ({ openExternal: vi.fn() }));
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  getSettings: vi.fn(),
}));
// The provider gallery has its own tests; here it only needs to render empty.
vi.mock("../providers/ProviderSetup", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../providers/ProviderSetup")>()),
  useProviderSetup: () => ({
    providers: [],
    ordered: [],
    refreshProviders: async () => {},
    sel: null,
    info: undefined,
    fields: {},
    setFieldValue: () => {},
    dirty: false,
    verify: { state: "idle" },
    showEndpoint: false,
    setShowEndpoint: () => {},
    keylessOk: new Set(),
    credentialed: false,
    savedState: false,
    secretFilled: false,
    openProvider: () => {},
    backToGallery: () => {},
    runTestAndSave: async () => true,
    removeKey: async () => {},
    cancelBackTimer: () => {},
    statusFor: () => null,
    saveField: async () => {},
    fieldSaved: null,
  }),
}));

import { getSettings } from "../api";
import { ModelsTab } from "./ManageTabs";

const SETTINGS = {
  provider: "openai",
  model: "gpt-4o",
  models: ["gpt-4o"],
  has_key: true,
  model_ready: true,
  source: "store",
  onboarded: true,
  surfaces: { cowork: true, chat: false, code: true },
  scratch_base: "~/OpenWorker",
  secrets_path: "/tmp/secrets.json",
} as unknown as ModelSettings;

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

afterEach(cleanup);

describe("ModelsTab when settings cannot load", () => {
  it("shows the error line with Retry instead of Loading…", async () => {
    vi.mocked(getSettings).mockRejectedValue(new TypeError("Failed to fetch"));
    render(<ModelsTab />);
    await flush();
    expect(screen.getByTestId("settings-error").textContent).toContain("Couldn't reach the server to load settings");
    expect(screen.getByTestId("settings-retry")).toBeTruthy();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("treats a timed-out (aborted) fetch the same way", async () => {
    vi.mocked(getSettings).mockRejectedValue(new DOMException("aborted", "AbortError"));
    render(<ModelsTab />);
    await flush();
    expect(screen.getByTestId("settings-error")).toBeTruthy();
  });

  it("Retry fetches again and clears the error once the server answers", async () => {
    vi.mocked(getSettings).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(SETTINGS);
    render(<ModelsTab />);
    await flush();
    expect(screen.getByTestId("settings-error")).toBeTruthy();

    const before = vi.mocked(getSettings).mock.calls.length;
    fireEvent.click(screen.getByTestId("settings-retry"));
    await flush();
    expect(vi.mocked(getSettings).mock.calls.length).toBe(before + 1);
    expect(screen.queryByTestId("settings-error")).toBeNull();
  });
});
