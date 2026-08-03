// Auth-method segmented choice + show_when field visibility (Bedrock's "Connect with"):
// only the selected method's fields render, and clicking a segment switches them.
// Plus the hook's keyless Detect contract (persist even when the form is clean).
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProviderForm, useProviderSetup, type ProviderSetupState } from "./ProviderSetup";
import * as api from "../api";
import type { ProviderInfo } from "../api";

vi.mock("../tauri", () => ({ openExternal: vi.fn() }));
vi.mock("../api", () => ({
  getProviders: vi.fn(),
  setProvider: vi.fn(),
  verifyProvider: vi.fn(),
  removeProvider: vi.fn(),
}));

afterEach(cleanup);

const BEDROCK: ProviderInfo = {
  name: "bedrock",
  title: "AWS Bedrock",
  needs_key: true,
  configured: false,
  values: {},
  suggested_models: [],
  recommended_model: null,
  fields: [
    { key: "region", label: "AWS region", secret: false, required: true, help: "", placeholder: "us-east-1" },
    {
      key: "auth_method",
      label: "Connect with",
      secret: false,
      required: false,
      help: "",
      placeholder: "",
      default: "api_key",
      choices: [
        { value: "api_key", label: "Bedrock API key" },
        { value: "profile", label: "AWS profile" },
        { value: "iam", label: "IAM keys" },
      ],
    },
    { key: "bedrock_api_key", label: "Bedrock API key", secret: true, required: false, help: "", placeholder: "ABSK…", show_when: { auth_method: "api_key" } },
    { key: "aws_profile", label: "AWS profile", secret: false, required: false, help: "", placeholder: "default", show_when: { auth_method: "profile" } },
    { key: "aws_secret_access_key", label: "Secret access key", secret: true, required: false, help: "", placeholder: "", show_when: { auth_method: "iam" } },
  ],
};

function makePs(fields: Record<string, string>, setFieldValue = vi.fn()): ProviderSetupState {
  return {
    providers: [BEDROCK],
    ordered: [BEDROCK],
    refreshProviders: async () => {},
    sel: "bedrock",
    info: BEDROCK,
    fields,
    setFieldValue,
    dirty: false,
    verify: { state: "idle" },
    showEndpoint: false,
    setShowEndpoint: () => {},
    keylessOk: new Set(),
    credentialed: false,
    savedState: false,
    secretFilled: true,
    openProvider: () => {},
    backToGallery: () => {},
    runTestAndSave: async () => true,
    removeKey: async () => {},
    cancelBackTimer: () => {},
    statusFor: () => null,
    saveField: async () => {},
    fieldSaved: null,
  };
}

describe("ProviderForm auth-method choice", () => {
  it("renders only the selected method's fields", () => {
    render(<ProviderForm ps={makePs({ auth_method: "api_key" })} tp="t" />);
    expect(screen.getByTestId("t-field-bedrock_api_key")).toBeTruthy();
    expect(screen.queryByTestId("t-field-aws_profile")).toBeNull();
    expect(screen.queryByTestId("t-field-aws_secret_access_key")).toBeNull();
    expect(screen.getByTestId("t-choice-auth_method-api_key").getAttribute("aria-checked")).toBe("true");
  });

  it("switching the segment swaps the visible fields", () => {
    const setFieldValue = vi.fn();
    const { rerender } = render(
      <ProviderForm ps={makePs({ auth_method: "api_key" }, setFieldValue)} tp="t" />,
    );
    fireEvent.click(screen.getByTestId("t-choice-auth_method-profile"));
    expect(setFieldValue).toHaveBeenCalledWith("auth_method", "profile");
    rerender(<ProviderForm ps={makePs({ auth_method: "profile" }, setFieldValue)} tp="t" />);
    expect(screen.getByTestId("t-field-aws_profile")).toBeTruthy();
    expect(screen.queryByTestId("t-field-bedrock_api_key")).toBeNull();
  });

  it("iam segment shows the key-pair fields", () => {
    render(<ProviderForm ps={makePs({ auth_method: "iam" })} tp="t" />);
    expect(screen.getByTestId("t-field-aws_secret_access_key")).toBeTruthy();
    expect(screen.queryByTestId("t-field-bedrock_api_key")).toBeNull();
  });
});

const LMSTUDIO: ProviderInfo = {
  name: "lmstudio",
  title: "LM Studio (local models)",
  needs_key: false,
  configured: true, // keyless providers always report configured — even before first use
  values: {},
  suggested_models: [],
  recommended_model: null,
  fields: [
    { key: "base_url", label: "LM Studio server URL", secret: false, required: false, help: "", placeholder: "http://localhost:1234" },
  ],
};

function HookHarness({ grab }: { grab: (ps: ProviderSetupState) => void }) {
  grab(useProviderSetup());
  return null;
}

describe("useProviderSetup keyless Detect", () => {
  it("persists a clean keyless form on a passing Detect", async () => {
    // Keyless providers report `configured` out of the box, so a dirty/configured gate
    // would skip the FIRST-time save — no stored profile, and the backend's
    // recommended-model auto-add would never run (codex review, 2026-08-03).
    vi.mocked(api.getProviders).mockResolvedValue([LMSTUDIO]);
    vi.mocked(api.verifyProvider).mockResolvedValue({ ok: true });
    vi.mocked(api.setProvider).mockResolvedValue({ ok: true });

    let ps!: ProviderSetupState;
    render(<HookHarness grab={(v) => (ps = v)} />);
    await waitFor(() => expect(ps.providers.length).toBe(1));
    act(() => ps.openProvider("lmstudio"));
    await act(async () => {
      expect(await ps.runTestAndSave()).toBe(true);
    });
    expect(api.setProvider).toHaveBeenCalledWith("lmstudio", { base_url: "" });
  });

  it("a failed save surfaces as an error, not '✓ Tested & saved'", async () => {
    vi.mocked(api.getProviders).mockResolvedValue([LMSTUDIO]);
    vi.mocked(api.verifyProvider).mockResolvedValue({ ok: true });
    vi.mocked(api.setProvider).mockResolvedValue({ ok: false, error: "disk full" });

    let ps!: ProviderSetupState;
    render(<HookHarness grab={(v) => (ps = v)} />);
    await waitFor(() => expect(ps.providers.length).toBe(1));
    act(() => ps.openProvider("lmstudio"));
    await act(async () => {
      expect(await ps.runTestAndSave()).toBe(false);
    });
    expect(ps.verify).toEqual({ state: "error", msg: "disk full" });
  });
});
