import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getConnectors,
  getRecentChannels,
  getSessionConnections,
  getSubscriptions,
  type Connector,
} from "../api";
import { AccessSection } from "./AccessSection";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getConnectors: vi.fn(),
    getRecentChannels: vi.fn(),
    getSessionConnections: vi.fn(),
    getSubscriptions: vi.fn(),
  };
});

vi.mock("../useRoots", () => ({
  useRoots: () => ({
    roots: [],
    busy: false,
    error: "",
    addRoot: vi.fn(),
    toggleAccess: vi.fn(),
    removeRoot: vi.fn(),
  }),
}));

const connector = (name: string, title: string): Connector =>
  ({
    name,
    title,
    icon: "",
    blurb: `${title} source`,
    auth: "none",
    two_way: false,
    channels: false,
    available: true,
    fields: [],
    instructions: [],
    connected: true,
    account: null,
    enabled: true,
    brand_color: "#6b7280",
    logo: name,
    allowed_users: [],
    tools: [],
    managed: false,
    managed_profile: false,
  }) as Connector;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AccessSection source boundaries", () => {
  it("does not render or offer Browser from a legacy sidecar payload", async () => {
    vi.mocked(getSessionConnections).mockResolvedValue({
      connected: [
        { connector: "browser", enabled: true, detail: "Browser" },
        { connector: "slack", enabled: true, detail: "Slack" },
      ],
      recommended: [
        {
          connector: "browser",
          reason: "Legacy Browser Use recommendation",
          tier: "core",
          connected: false,
        },
      ],
      attention: 1,
    });
    vi.mocked(getConnectors).mockResolvedValue([
      connector("browser", "Browser"),
      connector("slack", "Slack"),
    ]);
    vi.mocked(getSubscriptions).mockResolvedValue([]);
    vi.mocked(getRecentChannels).mockResolvedValue([]);

    render(<AccessSection sessionId="session-1" personaId="cowork" />);

    await waitFor(() => {
      expect(screen.getByTestId("access-summary").textContent).toBe("Slack");
    });
    expect(screen.queryByText("Browser")).toBeNull();

    fireEvent.click(screen.getByTestId("access-toggle"));
    await waitFor(() => expect(screen.getAllByText("Slack").length).toBeGreaterThan(1));
    expect(screen.queryByText("Browser")).toBeNull();

    fireEvent.click(screen.getByTestId("access-add-source"));
    expect(screen.queryByTestId("access-add-browser")).toBeNull();
    expect(screen.getByText(/No match/)).toBeTruthy();
  });
});
