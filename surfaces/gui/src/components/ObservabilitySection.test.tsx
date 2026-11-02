// Settings ▸ Machine ▸ Observability (OB-6, mocks UX-059 ob6 and ob6b). The page reads the
// machine's settings and status, writes back what changes, sends keys once and never shows
// them again (only the masked hints the machine returns), reports a refused key in plain
// words, and turns read-only when a company policy manages it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import i18n from "i18next";
import zh from "../locales/zh.json";

const base = {
  record: true,
  retention_days: 30,
  destination: { kind: "off" },
  include: { messages: false, tool_io: false, context: false, names: false },
  secrets: {},
  managed: null,
  notice: null,
};
let snapshot: any = { ...base };
let status: any = { state: "off", last_sent_at: null, sent: 0, sent_today: 0, failed: 0, dropped: 0, last_error: "" };

// Like the machine: keys go in, masked hints come back, a managed page refuses changes.
const setObservabilitySettings = vi.fn(async (patch: any) => {
  if (snapshot.managed && ("include" in patch || "destination" in patch)) return { ok: false, error: "Managed by Acme Corp" };
  const next = { ...snapshot, ...patch, include: { ...snapshot.include, ...(patch.include || {}) } };
  delete next.secrets;
  next.secrets = { ...snapshot.secrets };
  if (patch.secrets?.public_key) next.secrets.public_key = "pk-lf-••••••••3a91";
  if (patch.secrets?.secret_key) next.secrets.secret_key = "sk-lf-••••••••";
  snapshot = next;
  return { ok: true, ...snapshot };
});
const sendObservabilityTest = vi.fn(async () => ({ ok: false, status: 401, text: "The destination refused the key", ms: 12 }));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getObservabilitySettings: vi.fn(async () => snapshot),
    setObservabilitySettings: (patch: any) => setObservabilitySettings(patch),
    getObservabilityStatus: vi.fn(async () => status),
    sendObservabilityTest: () => sendObservabilityTest(),
    getMachineSessions: vi.fn(async () => [{ session_id: "s1", title: "Quarterly notes" }]),
    downloadSessionRecording: vi.fn(async () => true),
  };
});

import { ObservabilitySection } from "./ObservabilitySection";

const lastPatch = () => {
  const calls = setObservabilitySettings.mock.calls;
  return calls[calls.length - 1]?.[0];
};

describe("Settings ▸ Observability", () => {
  beforeEach(() => {
    snapshot = { ...base };
    status = { state: "off", last_sent_at: null, sent: 0, sent_today: 0, failed: 0, dropped: 0, last_error: "" };
    setObservabilitySettings.mockClear();
    sendObservabilityTest.mockClear();
  });
  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage("en");
  });

  it("Off by default: nothing leaves, the content switches are off, the status says so", async () => {
    render(<ObservabilitySection />);
    await screen.findByTestId("obs-section");
    expect(screen.getByTestId("obs-dest-off").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Nothing leaves this machine")).toBeTruthy();
    for (const k of ["messages", "tool_io", "context", "names"]) {
      expect(within(screen.getByTestId(`obs-include-${k}`)).getByRole("switch").getAttribute("aria-checked")).toBe("false");
    }
    expect(screen.getByTestId("obs-keep-30").getAttribute("aria-checked")).toBe("true");
    await waitFor(() => expect(screen.getByTestId("obs-status-pill").textContent).toBe("Off"));
    expect(screen.getByText(/Never sent, whatever is on/)).toBeTruthy();
  });

  it("Langfuse: the address and keys are saved once; keys come back masked, never shown", async () => {
    render(<ObservabilitySection />);
    await screen.findByTestId("obs-section");
    fireEvent.click(screen.getByTestId("obs-dest-langfuse"));
    expect(setObservabilitySettings).not.toHaveBeenCalled(); // no address yet
    const card = screen.getByTestId("obs-langfuse");
    fireEvent.change(within(card).getByLabelText("Langfuse address"), { target: { value: "https://langfuse.acme.internal" } });
    fireEvent.change(within(card).getByLabelText("Public key"), { target: { value: "pk-lf-1234567890ab3a91" } });
    fireEvent.change(within(card).getByLabelText("Secret key"), { target: { value: "sk-lf-very-secret" } });
    fireEvent.blur(within(card).getByLabelText("Secret key"));
    await waitFor(() =>
      expect(lastPatch()).toEqual({
        destination: { kind: "langfuse", host: "https://langfuse.acme.internal" },
        secrets: { public_key: "pk-lf-1234567890ab3a91", secret_key: "sk-lf-very-secret" },
      }),
    );
    // The fields empty again; the hints are what the machine sent back.
    await waitFor(() => expect((within(card).getByLabelText("Secret key") as HTMLInputElement).value).toBe(""));
    expect((within(card).getByLabelText("Public key") as HTMLInputElement).placeholder).toBe("pk-lf-••••••••3a91");
    expect((within(card).getByLabelText("Secret key") as HTMLInputElement).placeholder).toContain("sk-lf-••••••••");
    expect(document.body.textContent).not.toContain("sk-lf-very-secret");
    // A wrong key reads plainly.
    fireEvent.click(within(card).getByTestId("obs-test"));
    await waitFor(() => expect(screen.getByTestId("obs-test-result").textContent).toBe("The destination refused the key"));
  });

  it("a switch writes only its own field; the metadata row stays locked", async () => {
    snapshot = { ...base, destination: { kind: "otlp", url: "https://otel.example.com/v1/traces", header_names: ["x-team"] },
      secrets: { headers: { "x-team": "••••••••" } } };
    status = { state: "sending", last_sent_at: Date.now() / 1000 - 12, sent: 2000, sent_today: 1284, failed: 0, dropped: 0, last_error: "" };
    render(<ObservabilitySection />);
    await screen.findByTestId("obs-otlp");
    fireEvent.click(within(screen.getByTestId("obs-include-tool_io")).getByRole("switch"));
    await waitFor(() => expect(lastPatch()).toEqual({ include: { tool_io: true } }));
    const locked = within(screen.getByTestId("obs-include-metadata")).getByRole("switch") as HTMLButtonElement;
    expect(locked.disabled && locked.getAttribute("aria-checked") === "true").toBe(true);
    await waitFor(() => expect(screen.getByTestId("obs-status").textContent).toContain("Today: 1,284 steps sent · 0 failed · 0 dropped."));
    expect(screen.getByTestId("obs-status").textContent).toContain("Last sent 12 seconds ago");
    expect(screen.getByTestId("obs-status-pill").textContent).toBe("Sending");
  });

  it("a refused key shows in the status, and Record steps off is one switch", async () => {
    snapshot = { ...base, destination: { kind: "langfuse", host: "https://cloud.langfuse.com" } };
    status = { state: "failed", last_sent_at: null, sent: 0, sent_today: 0, failed: 1, dropped: 0, last_error: "The destination refused the key" };
    render(<ObservabilitySection />);
    await screen.findByTestId("obs-langfuse");
    await waitFor(() => expect(screen.getByTestId("obs-status-error").textContent).toBe("The destination refused the key"));
    expect(screen.getByTestId("obs-status-pill").textContent).toBe("Failed");
    fireEvent.click(within(screen.getByTestId("obs-record")).getByRole("switch"));
    await waitFor(() => expect(lastPatch()).toEqual({ record: false }));
    fireEvent.click(screen.getByTestId("obs-keep-7"));
    await waitFor(() => expect(lastPatch()).toEqual({ retention_days: 7 }));
  });

  it("managed by a company policy: read-only, says who set it and what goes where", async () => {
    snapshot = { ...base, destination: { kind: "langfuse", host: "https://langfuse.acme.internal" },
      include: { messages: true, tool_io: false, context: false, names: true },
      managed: { by: "Acme Corp", name: "Security team default", at: "2026-10-12" } };
    render(<ObservabilitySection />);
    await screen.findByTestId("obs-managed");
    expect(screen.getByText("Managed by Acme Corp")).toBeTruthy();
    expect(screen.getByText("Langfuse · langfuse.acme.internal")).toBeTruthy();
    expect(screen.getByText('Set by policy "Security team default" · 2026-10-12')).toBeTruthy();
    const switches = screen.getAllByRole("switch") as HTMLButtonElement[];
    expect(switches.every((s) => s.disabled)).toBe(true);
    expect(switches.map((s) => s.getAttribute("aria-checked"))).toEqual(["true", "true", "false", "false", "true"]);
    expect(screen.getByTestId("obs-managed-callout").textContent).toBe(
      "Your messages and the AI's answers and session and user names from this machine are sent to Acme Corp's Langfuse. A small notice appears in each new session.",
    );
    expect(screen.queryByTestId("obs-dest-langfuse")).toBeNull();
    expect(screen.queryByLabelText("Secret key")).toBeNull();
  });

  it("reads in Chinese", async () => {
    i18n.addResourceBundle("zh", "translation", zh, true, true);
    await i18n.changeLanguage("zh");
    render(<ObservabilitySection />);
    await screen.findByTestId("obs-section");
    expect(screen.getByText("把记录发送到")).toBeTruthy();
    expect(screen.getByText("不会有任何内容离开这台机器")).toBeTruthy();
    expect(screen.getByTestId("obs-keep-30").textContent).toBe("30 天");
  });
});
