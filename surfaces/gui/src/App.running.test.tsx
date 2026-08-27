import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const { SessionMock, Stub } = vi.hoisted(() => {
  const Stub = (name: string) => () => <div data-testid={name} />;
  const SessionMock = vi.fn(function (this: any, sessionId: string, workspace: string, agent: string, handlers: any) {
    setTimeout(() => {
      handlers.onEvent({
        type: "ready",
        data: {
          session_id: sessionId,
          agent,
          model: "gpt-5.6-sol",
          mode: "interactive",
          running: true,
          workspace: workspace || "/tmp/openworker",
        },
      });
    }, 0);
    return {
      close: vi.fn(),
      userMessage: vi.fn(),
      interrupt: vi.fn(),
      retry: vi.fn(),
      setModel: vi.fn(),
      approvalDecision: vi.fn(),
      directoryDecision: vi.fn(),
      questionAnswer: vi.fn(),
    };
  });
  return { SessionMock, Stub };
});

vi.mock("./tauri", () => ({
  isTauri: () => false,
  platformOS: () => "linux",
  startWindowDrag: vi.fn(),
}));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    Session: SessionMock,
    announceAutomationsChanged: vi.fn(),
    announceInboxUnlock: vi.fn(),
    connectEvents: vi.fn(() => () => {}),
    deleteSession: vi.fn(async () => ({ ok: true })),
    finalizeAutomationRun: vi.fn(async () => {}),
    getArtifacts: vi.fn(async () => []),
    getHealth: vi.fn(async () => ({
      status: "ok",
      default_workspace: null,
      model: "gpt-5.6-sol",
    })),
    getInbox: vi.fn(async () => []),
    getPersonas: vi.fn(async () => [
      {
        id: "cowork",
        name: "OpenWorker",
        icon: "cowork",
        tagline: "general assistant",
        family: "knowledge",
        enabled: true,
        surfaced: true,
        default: true,
      },
      {
        id: "code",
        name: "Code",
        icon: "code",
        tagline: "repository work",
        family: "code",
        enabled: true,
        surfaced: true,
        default: false,
      },
    ]),
    getRecentWorkspaces: vi.fn(async () => [{ path: "/tmp/openworker", name: "openworker", exists: true }]),
    getSessionMessages: vi.fn(async () => []),
    getSessions: vi.fn(async () => [
      {
        session_id: "s-1",
        title: "Busy session",
        workspace: "/tmp/openworker",
        agent: "cowork",
        model: "gpt-5.6-sol",
        mode: "interactive",
        updated_at: "2026-07-31 09:00:00",
        messages: 0,
        pinned: false,
        archived: false,
      },
    ]),
    getSettings: vi.fn(async () => ({
      provider: "openai",
      model: "gpt-5.6-sol",
      models: ["gpt-5.6-sol"],
      has_key: true,
      model_ready: true,
      source: "store",
      onboarded: true,
      surfaces: { cowork: true, chat: false, code: false },
      scratch_base: "/tmp/openworker",
      secrets_path: "/tmp/openworker/secrets.json",
      nav_layout: "flat",
      sessions_peek: 5,
      context_bar: false,
      model_labels: { "gpt-5.6-sol": "GPT-5.6 · OpenAI" },
      model_context_windows: { "gpt-5.6-sol": 200000 },
      pdf_fallback: "text",
      pdf_max_pages: 20,
      pdf_max_mb: 10,
    })),
    getUnattended: vi.fn(async () => false),
    renameSession: vi.fn(async () => ({ ok: true })),
    resolveInboxItem: vi.fn(async () => ({ ok: true })),
    runAutomation: vi.fn(async () => ({ ok: true })),
    setSessionFlags: vi.fn(async () => ({ ok: true })),
    setUnattended: vi.fn(async () => ({ ok: true })),
  };
});

vi.mock("./components/Sidebar", () => ({ Sidebar: Stub("sidebar") }));
vi.mock("./components/Transcript", () => ({ Transcript: Stub("transcript"), ThinkingBlock: Stub("thinking") }));
vi.mock("./components/SessionIntro", () => ({ SessionIntro: Stub("session-intro") }));
vi.mock("./components/RightRail", () => ({ RightRail: Stub("right-rail") }));
vi.mock("./components/UpdateBanner", () => ({ UpdateBanner: Stub("update-banner") }));
vi.mock("./components/SearchModal", () => ({ SearchModal: Stub("search-modal") }));
vi.mock("./components/Onboarding", () => ({ Onboarding: Stub("onboarding") }));
vi.mock("./components/FolderGate", () => ({ FolderGate: Stub("folder-gate") }));
vi.mock("./components/ScheduledView", () => ({ ScheduledView: Stub("scheduled-view") }));
vi.mock("./components/IntegrationsView", () => ({ IntegrationsView: Stub("integrations-view") }));
vi.mock("./components/SettingsView", () => ({ SettingsView: Stub("settings-view") }));
vi.mock("./components/PersonaView", () => ({ PersonaView: Stub("persona-view") }));
vi.mock("./components/AuditView", () => ({ AuditView: Stub("audit-view") }));
vi.mock("./components/InboxView", () => ({ InboxView: Stub("inbox-view") }));
vi.mock("./components/ApprovalCard", () => ({ ApprovalCard: Stub("approval-card") }));
vi.mock("./components/DirectoryRequestCard", () => ({ DirectoryRequestCard: Stub("directory-request-card") }));
vi.mock("./components/PlanCard", () => ({ PlanCard: Stub("plan-card") }));
vi.mock("./components/WorkspaceTrustPrompt", () => ({ WorkspaceTrustPrompt: Stub("workspace-trust") }));

import { App } from "./App";

describe("App session ready state", () => {
  beforeEach(() => {
    localStorage.clear();
    SessionMock.mockClear();
  });

  afterEach(() => {
    cleanup();
  });

  it("hydrates running from the session ready event", async () => {
    render(<App />);

    await waitFor(() => expect(SessionMock).toHaveBeenCalled());
    await screen.findByRole("button", { name: /Stop/ });
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });
});
