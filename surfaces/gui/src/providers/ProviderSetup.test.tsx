// Auth-method segmented choice + show_when field visibility (Bedrock's "Connect with"):
// only the selected method's fields render, and clicking a segment switches them.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ProviderForm, type ProviderSetupState } from "./ProviderSetup";
import type { ProviderInfo } from "../api";

vi.mock("../tauri", () => ({ openExternal: vi.fn() }));

afterEach(cleanup);

const BEDROCK: ProviderInfo = {
  name: "bedrock",
  title: "AWS Bedrock",
  needs_key: true,
  configured: false,
  enabled: true,
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
    toggleEnabled: async () => {},
    cancelBackTimer: () => {},
    statusFor: () => null,
    saveField: async () => {},
    fieldSaved: null,
  };
}

const OPENAI: ProviderInfo = {
  name: "openai",
  title: "OpenAI",
  needs_key: true,
  configured: true,
  enabled: true,
  values: {},
  suggested_models: [],
  recommended_model: null,
  key_set_at: "2026-07-01",
  fields: [{ key: "api_key", label: "OpenAI API key", secret: true, required: true, help: "", placeholder: "sk-…" }],
};

function makeOpenAiPs(enabled: boolean): ProviderSetupState {
  const info = { ...OPENAI, enabled };
  return { ...makePs({}), providers: [info], ordered: [info], sel: "openai", info };
}

describe("ProviderForm on/off toggle", () => {
  it("only renders once the provider is configured", () => {
    render(<ProviderForm ps={makePs({})} tp="t" />); // BEDROCK: configured=false
    expect(screen.queryByTestId("t-toggle-enabled")).toBeNull();
  });

  it("positions the knob with an explicit left, not the browser's centered static position", () => {
    // Regression (owner catch 2026-07-30): the knob had no explicit `left`, so a bare
    // `<button>`'s default `text-align: center` UA style put its static position at the
    // track's midpoint; `translate-x` was then added on top of that, pushing the knob
    // outside the track instead of sliding it within it. jsdom doesn't lay out real
    // pixels, so this pins the fix at the class level — surfaces/gui/e2e/provider-keys.spec.ts
    // pins the real geometry in an actual browser.
    const { container } = render(<ProviderForm ps={makeOpenAiPs(true)} tp="t" />);
    const knob = container.querySelector('[data-testid="t-toggle-enabled"] span')!;
    expect(knob.className).toContain("left-0.5");
    expect(knob.className).not.toMatch(/translate-x-\[18px\]/);
  });

  it("flips aria-checked and the translate class with the enabled prop", () => {
    const { container: onC } = render(<ProviderForm ps={makeOpenAiPs(true)} tp="t" />);
    const onToggle = screen.getByTestId("t-toggle-enabled");
    expect(onToggle.getAttribute("aria-checked")).toBe("true");
    expect(onC.querySelector('[data-testid="t-toggle-enabled"] span')!.className).toContain("translate-x-4");
    cleanup();

    const { container: offC } = render(<ProviderForm ps={makeOpenAiPs(false)} tp="t" />);
    const offToggle = screen.getByTestId("t-toggle-enabled");
    expect(offToggle.getAttribute("aria-checked")).toBe("false");
    expect(offC.querySelector('[data-testid="t-toggle-enabled"] span')!.className).toContain("translate-x-0");
  });
});

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
