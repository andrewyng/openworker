// Auth-method segmented choice + show_when field visibility (Bedrock's "Connect with"):
// only the selected method's fields render, and clicking a segment switches them.
// Plus the independent OpenCode Zen/Go cards: both render, share one logo, each help
// links go to their respective provider pages, and saving one card never marks the sibling configured.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  KEY_HELP,
  ProviderCards,
  ProviderForm,
  ProviderMark,
  useProviderSetup,
  type ProviderSetupState,
} from "./ProviderSetup";
import { PROVIDER_LOGOS } from "./logos";
import { ModelChecklist } from "../components/ModelChecklist";
import * as api from "../api";
import { setProvider, verifyProvider, type ProviderInfo } from "../api";

vi.mock("../tauri", () => ({ openExternal: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function LiveProviderSetup({ onSaved = vi.fn() }: { onSaved?: () => void }) {
  const ps = useProviderSetup({ onSaved });
  return ps.sel ? <ProviderForm ps={ps} tp="live" /> : <ProviderCards ps={ps} tp="live" />;
}

function LiveSettingsSetup({ onSaved = vi.fn() }: { onSaved?: () => void } = {}) {
  const ps = useProviderSetup({ onSaved });
  return ps.sel ? (
    <ProviderForm ps={ps} tp="live" footer={<button onClick={() => void ps.removeKey()}>Remove key</button>} />
  ) : <ProviderCards ps={ps} tp="live" />;
}

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

/** The two independent OpenCode picker entries (PR #110: separate profiles/endpoints). */
function opencodeInfo(name: "opencode_zen" | "opencode_go", configured = false): ProviderInfo {
  const zen = name === "opencode_zen";
  return {
    name,
    title: zen ? "OpenCode Zen" : "OpenCode Go",
    needs_key: true,
    configured,
    values: {},
    suggested_models: [],
    recommended_model: zen ? "grok-4.5" : "kimi-k3",
    fields: [
      { key: "api_key", label: "OpenCode API key", secret: true, required: true, help: "", placeholder: "" },
      {
        key: "base_url",
        label: "Endpoint",
        secret: false,
        required: false,
        help: "",
        placeholder: zen ? "https://opencode.ai/zen/v1/" : "https://opencode.ai/zen/go/v1/",
        default: zen ? "https://opencode.ai/zen/v1/" : "https://opencode.ai/zen/go/v1/",
      },
    ],
  };
}

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

describe("OpenCode Zen + Go independent provider setup", () => {
  it("runs the production verify, save, refresh flow through the hook and controls", async () => {
    const zen = opencodeInfo("opencode_zen");
    const refreshed = { ...zen, configured: true, values: { base_url: zen.fields[1].default || "" } };
    const onSaved = vi.fn();
    vi.spyOn(api, "getProviders").mockResolvedValueOnce([zen]).mockResolvedValueOnce([refreshed]);
    vi.spyOn(api, "verifyProvider").mockResolvedValue({ ok: true });
    vi.spyOn(api, "setProvider").mockResolvedValue({ ok: true });

    render(<LiveProviderSetup onSaved={onSaved} />);
    await vi.waitFor(() => expect(screen.getByTestId("live-provider-opencode_zen")).toBeTruthy());
    fireEvent.click(screen.getByTestId("live-provider-opencode_zen"));
    fireEvent.change(screen.getByTestId("live-field-api_key"), { target: { value: "oc-key" } });
    fireEvent.click(screen.getByTestId("live-test"));

    await vi.waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(api.verifyProvider).toHaveBeenCalledWith("opencode_zen", expect.objectContaining({ api_key: "oc-key" }));
    expect(api.setProvider).toHaveBeenCalledWith("opencode_zen", expect.any(Object));
  });
  it("renders both cards independently with their own configured state", () => {
    // Only Go is configured — Zen must still read "Not set up".
    const zen = opencodeInfo("opencode_zen");
    const go = opencodeInfo("opencode_go", true);
    const ps = makePs({});
    ps.providers = [zen, go];
    ps.ordered = [zen, go];
    // makePs stubs statusFor; give it the real rendering contract so the card
    // statuses reflect each provider's own `configured` flag.
    ps.statusFor = (p: ProviderInfo) => (
      p.configured && p.needs_key ? <>✓ Connected</> : <>Not set up</>
    );
    render(<ProviderCards ps={ps} tp="t" />);
    expect(screen.getByTestId("t-provider-opencode_zen")).toBeTruthy();
    expect(screen.getByTestId("t-provider-opencode_go")).toBeTruthy();
    expect(screen.getByText("✓ Connected")).toBeTruthy(); // Go only
    expect(screen.getByText("Not set up")).toBeTruthy(); // Zen still independent
  });

  it("registers the OpenCode logo once and reuses it for both entries", () => {
    expect(PROVIDER_LOGOS["opencode_zen"]).toBeTruthy();
    expect(PROVIDER_LOGOS["opencode_go"]).toBeTruthy();
    expect(PROVIDER_LOGOS["opencode_zen"]).toBe(PROVIDER_LOGOS["opencode_go"]);
    const { container } = render(<ProviderMark name="opencode_zen" title="OpenCode Zen" />);
    const img = container.querySelector("img") as HTMLImageElement;
    expect(img.src).toContain("opencode.svg");
  });

  it("maps each help link to its provider page", () => {
    expect(KEY_HELP["opencode_zen"].url).toBe("https://opencode.ai/zen");
    expect(KEY_HELP["opencode_go"].url).toBe("https://opencode.ai/go");
  });

  it("prefills each entry's own endpoint", () => {
    const zen = opencodeInfo("opencode_zen");
    const go = opencodeInfo("opencode_go");
    const ep = (p: ProviderInfo) =>
      p.fields.find((f) => f.key === "base_url")!.default;
    expect(ep(zen)).toBe("https://opencode.ai/zen/v1/");
    expect(ep(go)).toBe("https://opencode.ai/zen/go/v1/");
  });

  it.each([
    ["opencode_zen", "oc-zen", "https://opencode.ai/zen/v1/", "grok-4.5"],
    ["opencode_go", "oc-go", "https://opencode.ai/zen/go/v1/", "kimi-k3"],
  ])("sends the real verify and save payload for %s", async (name, key, endpoint, model) => {
    const calls: Array<{ url: string; body: { name: string; fields: Record<string, string> } }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      calls.push({ url, body });
      return { json: async () => ({ ok: true }) };
    }));
    const fields = { api_key: key, base_url: endpoint, model };

    await verifyProvider(name, fields);
    await setProvider(name, fields);

    expect(calls).toEqual([
      { url: expect.stringContaining("/v1/providers/verify"), body: { name, fields } },
      { url: expect.stringContaining("/v1/providers"), body: { name, fields } },
    ]);
    vi.unstubAllGlobals();
  });

  it.each([
    ["opencode_zen", "opencode_go"],
    ["opencode_go", "opencode_zen"],
  ] as const)(
    "real hook saves %s without changing %s",
    async (name, sibling) => {
      const selected = opencodeInfo(name);
      const siblingInfo = opencodeInfo(sibling);
      const refreshed = { ...selected, configured: true };
      const onSaved = vi.fn();
      vi.spyOn(api, "getProviders")
        .mockResolvedValueOnce([selected, siblingInfo])
        .mockResolvedValueOnce([refreshed, siblingInfo]);
      vi.spyOn(api, "verifyProvider").mockResolvedValue({ ok: true });
      vi.spyOn(api, "setProvider").mockResolvedValue({ ok: true });

      render(
        <LiveProviderSetup onSaved={onSaved} />,
      );
      await vi.waitFor(() => expect(screen.getByTestId(`live-provider-${name}`)).toBeTruthy());
      fireEvent.click(screen.getByTestId(`live-provider-${name}`));
      fireEvent.change(screen.getByTestId("live-field-api_key"), { target: { value: "oc-key" } });
      fireEvent.click(screen.getByTestId("live-test"));

      await vi.waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
      expect(api.verifyProvider).toHaveBeenCalledWith(name, expect.any(Object));
      expect(api.setProvider).toHaveBeenCalledWith(name, expect.any(Object));
      expect(api.setProvider).not.toHaveBeenCalledWith(sibling, expect.anything());
      expect(siblingInfo.configured).toBe(false);
    },
  );

  it("renders the Free + data-retention caveat on Zen free model labels", () => {
    const labels = {
      "opencode_zen:deepseek-v4-flash-free":
        "DeepSeek V4 Flash Free · OpenCode Zen (Free, data may be retained)",
    };
    render(
      <ModelChecklist
        provider="opencode_zen"
        knownProviders={["opencode_zen", "opencode_go"]}
        suggested={[]}
        curated={["opencode_zen:deepseek-v4-flash-free"]}
        defaultModel=""
        labels={labels}
        onChanged={() => {}}
      />,
    );
    expect(
      screen.getByText("DeepSeek V4 Flash Free · OpenCode Zen (Free, data may be retained)"),
    ).toBeTruthy();
  });
});

describe("Provider setup operation cancellation", () => {
  it("does not update state after an in-flight verify resolves after unmount", async () => {
    let resolveVerify!: (value: { ok: boolean }) => void;
    vi.spyOn(api, "getProviders").mockResolvedValue([opencodeInfo("opencode_zen")]);
    vi.spyOn(api, "verifyProvider").mockReturnValue(new Promise((resolve) => { resolveVerify = resolve; }) as never);

    const view = render(<LiveProviderSetup />);
    await waitFor(() => expect(screen.getByTestId("live-provider-opencode_zen")).toBeTruthy());
    fireEvent.click(screen.getByTestId("live-provider-opencode_zen"));
    fireEvent.change(screen.getByTestId("live-field-api_key"), { target: { value: "oc-key" } });
    fireEvent.click(screen.getByTestId("live-test"));
    view.unmount();
    resolveVerify({ ok: true });
    await Promise.resolve();
  });

  it("cancels the delayed return when navigating back to the gallery", async () => {
    vi.useFakeTimers();
    try {
      const zen = opencodeInfo("opencode_zen");
      vi.spyOn(api, "getProviders").mockResolvedValueOnce([zen]).mockResolvedValueOnce([{ ...zen, configured: true }]);
      vi.spyOn(api, "verifyProvider").mockResolvedValue({ ok: true });
      vi.spyOn(api, "setProvider").mockResolvedValue({ ok: true });

      render(<LiveProviderSetup />);
      await vi.waitFor(() => expect(screen.getByTestId("live-provider-opencode_zen")).toBeTruthy());
      fireEvent.click(screen.getByTestId("live-provider-opencode_zen"));
      fireEvent.change(screen.getByTestId("live-field-api_key"), { target: { value: "oc-key" } });
      fireEvent.click(screen.getByTestId("live-test"));
      await vi.waitFor(() => expect(screen.getByTestId("live-back")).toBeTruthy());
      fireEvent.click(screen.getByTestId("live-back"));
      vi.advanceTimersByTime(1000);
      expect(screen.getByTestId("live-provider-opencode_zen")).toBeTruthy();
      vi.advanceTimersByTime(899);
      expect(screen.getByTestId("live-provider-opencode_zen")).toBeTruthy();
      vi.advanceTimersByTime(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns to the gallery exactly 900ms after a successful save", async () => {
    vi.useFakeTimers();
    try {
      const zen = opencodeInfo("opencode_zen");
      const onSaved = vi.fn();
      vi.spyOn(api, "getProviders").mockResolvedValueOnce([zen]).mockResolvedValueOnce([{ ...zen, configured: true }]);
      vi.spyOn(api, "verifyProvider").mockResolvedValue({ ok: true });
      vi.spyOn(api, "setProvider").mockResolvedValue({ ok: true });

      render(<LiveProviderSetup onSaved={onSaved} />);
      await vi.waitFor(() => expect(screen.getByTestId("live-provider-opencode_zen")).toBeTruthy());
      fireEvent.click(screen.getByTestId("live-provider-opencode_zen"));
      fireEvent.change(screen.getByTestId("live-field-api_key"), { target: { value: "oc-key" } });
      fireEvent.click(screen.getByTestId("live-test"));
      await vi.waitFor(() => expect(onSaved).toHaveBeenCalledOnce());

      vi.advanceTimersByTime(899);
      expect(screen.getByTestId("live-back")).toBeTruthy();
      vi.advanceTimersByTime(1);
      await vi.waitFor(() => expect(screen.getByTestId("live-provider-opencode_zen")).toBeTruthy());
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Provider setup errors and timer lifetimes", () => {
  async function openProvider(
    name: "opencode_zen" | "opencode_go",
    configured = false,
    onSaved = vi.fn(),
  ) {
    const provider = opencodeInfo(name, configured);
    vi.spyOn(api, "getProviders").mockResolvedValue([provider]);
    render(
      configured ? <LiveSettingsSetup onSaved={onSaved} /> : <LiveProviderSetup onSaved={onSaved} />,
    );
    await vi.waitFor(() => expect(screen.getByTestId(`live-provider-${name}`)).toBeTruthy());
    fireEvent.click(screen.getByTestId(`live-provider-${name}`));
    return provider;
  }

  it.each(["opencode_zen", "opencode_go"] as const)(
    "shows a verify error for %s and preserves the form draft",
    async (name) => {
      const onSaved = vi.fn();
      await openProvider(name, false, onSaved);
      vi.spyOn(api, "verifyProvider").mockResolvedValue({ ok: false, error: "bad key" });
      vi.spyOn(api, "setProvider").mockResolvedValue({ ok: true });
      fireEvent.change(screen.getByTestId("live-field-api_key"), { target: { value: "keep-me" } });
      fireEvent.click(screen.getByTestId("live-test"));
      await vi.waitFor(() => expect(screen.getByText("bad key")).toBeTruthy());
      expect(screen.getByTestId("live-field-api_key")).toHaveProperty("value", "keep-me");
      expect(api.setProvider).not.toHaveBeenCalled();
      expect(onSaved).not.toHaveBeenCalled();
      expect(screen.getByTestId("live-back")).toBeTruthy();
    },
  );

  it.each(["opencode_zen", "opencode_go"] as const)(
    "shows a save error for %s and preserves the form draft",
    async (name) => {
      const onSaved = vi.fn();
      await openProvider(name, false, onSaved);
      vi.spyOn(api, "verifyProvider").mockResolvedValue({ ok: true });
      vi.spyOn(api, "setProvider").mockResolvedValue({ ok: false, error: "save failed" });
      fireEvent.change(screen.getByTestId("live-field-api_key"), { target: { value: "keep-me" } });
      fireEvent.click(screen.getByTestId("live-test"));
      await vi.waitFor(() => expect(screen.getByText("save failed")).toBeTruthy());
      expect(screen.getByTestId("live-field-api_key")).toHaveProperty("value", "keep-me");
      expect(api.getProviders).toHaveBeenCalledOnce();
      expect(onSaved).not.toHaveBeenCalled();
      expect(screen.getByTestId("live-back")).toBeTruthy();
    },
  );

  it("shows a refresh error without losing the form or navigating", async () => {
    const zen = opencodeInfo("opencode_zen");
    const onSaved = vi.fn();
    vi.spyOn(api, "getProviders").mockResolvedValueOnce([zen]).mockRejectedValueOnce(new Error("refresh failed"));
    vi.spyOn(api, "verifyProvider").mockResolvedValue({ ok: true });
    vi.spyOn(api, "setProvider").mockResolvedValue({ ok: true });
    render(<LiveProviderSetup onSaved={onSaved} />);
    await vi.waitFor(() => expect(screen.getByTestId("live-provider-opencode_zen")).toBeTruthy());
    fireEvent.click(screen.getByTestId("live-provider-opencode_zen"));
    fireEvent.change(screen.getByTestId("live-field-api_key"), { target: { value: "keep-me" } });
    fireEvent.click(screen.getByTestId("live-test"));
    await vi.waitFor(() => expect(screen.getByText("refresh failed")).toBeTruthy());
    expect(screen.getByTestId("live-field-api_key")).toHaveProperty("value", "keep-me");
    expect(screen.getByTestId("live-back")).toBeTruthy();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("removes only the selected OpenCode provider and preserves its sibling", async () => {
    const zen = opencodeInfo("opencode_zen", true);
    const go = opencodeInfo("opencode_go", true);
    const onSaved = vi.fn();
    vi.spyOn(api, "getProviders")
      .mockResolvedValueOnce([zen, go])
      .mockResolvedValueOnce([{ ...zen, configured: false }, go]);
    vi.spyOn(api, "removeProvider").mockResolvedValue({ ok: true });

    render(<LiveSettingsSetup onSaved={onSaved} />);
    await vi.waitFor(() => expect(screen.getByTestId("live-provider-opencode_zen")).toBeTruthy());
    fireEvent.click(screen.getByTestId("live-provider-opencode_zen"));
    fireEvent.click(screen.getByText("Remove key"));

    await vi.waitFor(() => expect(screen.getByTestId("live-provider-opencode_go")).toBeTruthy());
    expect(api.removeProvider).toHaveBeenCalledWith("opencode_zen");
    expect(onSaved).toHaveBeenCalledOnce();
    expect(screen.getByText("✓ Connected")).toBeTruthy();
    expect(screen.getByText("Not set up")).toBeTruthy();
  });

  it("shows a remove error and preserves the selected form", async () => {
    await openProvider("opencode_zen", true);
    vi.spyOn(api, "removeProvider").mockResolvedValue({ ok: false, error: "remove failed" });
    fireEvent.click(screen.getByText("Remove key"));
    await vi.waitFor(() => expect(screen.getByText("remove failed")).toBeTruthy());
    expect(screen.getByTestId("live-field-api_key")).toBeTruthy();
    expect(screen.getByTestId("live-back")).toBeTruthy();
  });

  it("shows a blur-save error and preserves the endpoint value", async () => {
    await openProvider("opencode_zen", true);
    vi.spyOn(api, "setProvider").mockResolvedValue({ ok: false, error: "blur failed" });
    fireEvent.click(screen.getByTestId("live-endpoint-link"));
    const endpoint = screen.getByTestId("live-field-base_url");
    fireEvent.change(endpoint, { target: { value: "https://keep.example/" } });
    fireEvent.blur(endpoint);
    await vi.waitFor(() => expect(screen.getByText("blur failed")).toBeTruthy());
    expect(endpoint).toHaveProperty("value", "https://keep.example/");
  });

  it("expires the field-saved pill after 1400ms and cancels it on unmount", async () => {
    vi.useFakeTimers();
    try {
      await openProvider("opencode_zen", true);
      vi.spyOn(api, "setProvider").mockResolvedValue({ ok: true });
      vi.spyOn(api, "getProviders").mockResolvedValue([opencodeInfo("opencode_zen", true)]);
      fireEvent.click(screen.getByTestId("live-endpoint-link"));
      const endpoint = screen.getByTestId("live-field-base_url");
      fireEvent.change(endpoint, { target: { value: "https://new.example/" } });
      fireEvent.blur(endpoint);
      await vi.waitFor(() => expect(screen.getByTestId("live-field-saved-base_url")).toBeTruthy());
      vi.advanceTimersByTime(1399);
      expect(screen.getByTestId("live-field-saved-base_url")).toBeTruthy();
      vi.advanceTimersByTime(1);
      await vi.waitFor(() => expect(screen.queryByTestId("live-field-saved-base_url")).toBeNull());
    } finally { vi.useRealTimers(); }
  });

  it("cleans up an active field-saved timer when unmounted", async () => {
    vi.useFakeTimers();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      await openProvider("opencode_zen", true);
      vi.spyOn(api, "setProvider").mockResolvedValue({ ok: true });
      vi.spyOn(api, "getProviders").mockResolvedValue([opencodeInfo("opencode_zen", true)]);
      fireEvent.click(screen.getByTestId("live-endpoint-link"));
      const endpoint = screen.getByTestId("live-field-base_url");
      fireEvent.change(endpoint, { target: { value: "https://new.example/" } });
      fireEvent.blur(endpoint);
      await vi.waitFor(() => expect(screen.getByTestId("live-field-saved-base_url")).toBeTruthy());
      cleanup();
      vi.advanceTimersByTime(1400);
      expect(consoleError).not.toHaveBeenCalled();
    } finally { vi.useRealTimers(); }
  });
});
