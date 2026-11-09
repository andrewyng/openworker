// OB-8: Activity gains a tab bar, Log | Usage; the Usage tab asks the machine with the
// viewer's own day boundaries (TD3-2).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const empty = { groups: [], no_turn: { calls: 0, total: 0 }, not_checked: 0, kept_from: null };
vi.mock("../api", () => ({
  getAudit: vi.fn(async () => []),
  getUsageRollup: vi.fn(async () => empty),
  getHeaviestTurns: vi.fn(async () => ({ turns: [] })),
  getApproverStrip: vi.fn(async () => ({ shown: false })),
  getRatings: vi.fn(async () => ({ up: 0, down: 0, reasons: {}, down_turns: [], more: 0 })),
  getMachines: vi.fn(async () => ({ machines: [], armed: false })),
}));
vi.mock("./IntegrationsView", () => ({ PanelHead: ({ title }: { title: string }) => <h1>{title}</h1> }));

import { getUsageRollup } from "../api";
import { AuditView } from "./AuditView";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Activity tabs", () => {
  it("opens on the Log, and Usage loads the period's rollups with day boundaries", async () => {
    render(<AuditView />);
    expect(screen.getByTestId("activity-tab-log").getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByTestId("activity-tab-usage"));
    await waitFor(() => expect(screen.getByTestId("usage-tile-tokens")).toBeTruthy());
    const calls = (getUsageRollup as any).mock.calls.map((c: any[]) => c[0]);
    const byDay = calls.find((q: any) => q.group === "day,purpose");
    expect(byDay.days).toHaveLength(7);
    expect(byDay.from).toBe(byDay.days[0][1]);
    expect(byDay.to).toBe(byDay.days[6][2]);
    fireEvent.click(screen.getByTestId("usage-period-30d"));
    await waitFor(() => expect((getUsageRollup as any).mock.calls.some((c: any[]) => c[0].days?.length === 30)).toBe(true));
  });

  it("a call filter always opens the Log", () => {
    render(<AuditView callId="call_9" initialTab="usage" />);
    expect(screen.getByTestId("activity-tab-log").getAttribute("aria-selected")).toBe("true");
  });
});
