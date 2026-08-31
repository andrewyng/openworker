// Auth-method segmented choice + show_when field visibility (Bedrock's "Connect with"):
// only the selected method's fields render, and clicking a segment switches them.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { KEY_HELP, ProviderForm, ProviderMark, type ProviderSetupState } from "./ProviderSetup";
import type { ProviderInfo } from "../api";

vi.mock("../tauri", () => ({ openExternal: vi.fn() }));

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

const OPENAI: ProviderInfo = {
  name: "openai",
  title: "OpenAI",
  needs_key: true,
  configured: false,
  values: {},
  suggested_models: [],
  recommended_model: "gpt-5.6-sol",
  fields: [
    {
      key: "auth_method",
      label: "Connect with",
      secret: false,
      required: false,
      help: "",
      placeholder: "",
      default: "api_key",
      choices: [
        { value: "api_key", label: "API key" },
        { value: "azure_ad", label: "Microsoft Entra ID" },
      ],
    },
    { key: "api_key", label: "OpenAI API key", secret: true, required: true, help: "", placeholder: "sk-…", show_when: { auth_method: "api_key" } },
    { key: "tenant_id", label: "Tenant ID", secret: false, required: true, help: "", placeholder: "", show_when: { auth_method: "azure_ad" } },
    { key: "client_id", label: "Client ID", secret: false, required: true, help: "", placeholder: "", show_when: { auth_method: "azure_ad" } },
    { key: "client_secret", label: "Client secret", secret: true, required: true, help: "", placeholder: "", show_when: { auth_method: "azure_ad" } },
    { key: "base_url", label: "Custom endpoint (optional)", secret: false, required: false, help: "", placeholder: "https://…/openai/v1" },
  ],
};

function makePs(
  fields: Record<string, string>,
  setFieldValue = vi.fn(),
  provider: ProviderInfo = BEDROCK,
): ProviderSetupState {
  return {
    providers: [provider],
    ordered: [provider],
    refreshProviders: async () => {},
    sel: provider.name,
    info: provider,
    fields,
    setFieldValue,
    dirty: false,
    verify: { state: "idle" },
    showEndpoint: false,
    setShowEndpoint: () => {},
    keylessOk: new Set(),
    credentialed: false,
    activeCredentialed: false,
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

describe("ProviderForm Microsoft Entra ID choice", () => {
  it("keeps the existing API-key form as the default", () => {
    render(<ProviderForm ps={makePs({ auth_method: "api_key" }, vi.fn(), OPENAI)} tp="t" />);
    expect(screen.getByTestId("t-field-api_key")).toBeTruthy();
    expect(screen.queryByTestId("t-field-tenant_id")).toBeNull();
    expect(screen.getByText(/Create one at platform\.openai\.com/)).toBeTruthy();
  });

  it("shows only service-principal fields for Microsoft Entra ID", () => {
    render(<ProviderForm ps={makePs({ auth_method: "azure_ad" }, vi.fn(), OPENAI)} tp="t" />);
    expect(screen.getByTestId("t-field-tenant_id")).toBeTruthy();
    expect(screen.getByTestId("t-field-client_id")).toBeTruthy();
    expect(screen.getByTestId("t-field-client_secret")).toBeTruthy();
    expect(screen.queryByTestId("t-field-api_key")).toBeNull();
    expect(screen.queryByText(/Create one at platform\.openai\.com/)).toBeNull();
    expect(screen.getByTestId("t-endpoint-link")).toBeTruthy();
  });

  it("does not enable Test & save while the active required secret is empty", () => {
    const ps = makePs({ auth_method: "azure_ad" }, vi.fn(), OPENAI);
    ps.secretFilled = false;
    render(<ProviderForm ps={ps} tp="t" />);
    expect(screen.getByTestId("t-test").hasAttribute("disabled")).toBe(true);
  });
});

describe("Ark provider presentation", () => {
  it("uses separate BytePlus and Volcengine brand marks", () => {
    const { container, rerender } = render(
      <ProviderMark name="ark" title="BytePlus Ark" />,
    );
    expect(container.querySelector("img")).toBeTruthy();

    rerender(
      <ProviderMark
        name="ark-agent-plan-cn"
        title="Volcengine Ark Agent Plan"
      />,
    );
    expect(container.querySelector("img")).toBeTruthy();
  });

  it("links each provider to its own API key console", () => {
    expect(KEY_HELP.ark.url).toBe(
      "https://console.byteplus.com/ark/region:ark+ap-southeast-1/apiKey",
    );
    expect(KEY_HELP["ark-agent-plan-cn"].url).toContain(
      "advancedActiveKey=agentPlan",
    );
  });
});
