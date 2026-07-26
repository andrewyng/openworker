// SKILLS-SPEC §4.6 GUI — the rail's Skills group under Access: effective-menu rows with
// per-session MUTE toggles (connector parity), the collapsed-glance skills count, and the
// "Manage all skills →" deep link that carries the session's workspace (two doors).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AccessSection } from "./AccessSection";

type Call = { url: string; method: string; body: any };

const SKILLS = {
  skills: [
    { name: "weekly-report", description: "Monday status report", scope: "global", enabled: true },
    { name: "release-checklist", description: "repo release steps", scope: "project", enabled: true },
    { name: "muted-one", description: "off here", scope: "global", enabled: false },
  ],
};

function stubFetch(skillsJson: any = SKILLS) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (data: any) => ({ ok: true, json: async () => data }) as Response;
      if (url.includes("/skills") && method === "POST") {
        // Echo the mute: the endpoint returns the refreshed view.
        const body = JSON.parse(String(init?.body));
        return json({
          skills: skillsJson.skills.map((s: any) =>
            s.name === body.skill ? { ...s, enabled: body.enabled } : s,
          ),
        });
      }
      if (url.includes("/skills")) return json(skillsJson);
      if (url.includes("/connections")) return json({ connected: [], recommended: [], attention: 0 });
      if (url.includes("/v1/connectors")) return json({ connectors: [] });
      if (url.includes("/subscriptions")) return json({ subscriptions: [] });
      if (url.includes("/channels/recent")) return json({ channels: [] });
      if (url.includes("/roots")) return json({ roots: [] });
      return json({});
    }),
  );
  return calls;
}

const renderSection = (extra: Partial<Parameters<typeof AccessSection>[0]> = {}) =>
  render(
    <AccessSection sessionId="s1" workspace="C:\dev\payments" {...extra} />,
  );

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AccessSection skills group", () => {
  it("counts only skills that are ON in the collapsed glance", async () => {
    stubFetch();
    renderSection();
    await waitFor(() =>
      expect(screen.getByTestId("access-summary").textContent).toContain("2 skills"),
    );
  });

  it("lists effective-menu rows with scope and mute states when expanded", async () => {
    stubFetch();
    renderSection();
    fireEvent.click(screen.getByTestId("access-toggle"));
    await screen.findByTestId("rail-skills");
    expect(screen.getByText("weekly-report")).toBeTruthy();
    expect(screen.getByText("· project")).toBeTruthy();
    expect(screen.getByText("muted-one")).toBeTruthy(); // muted rows stay visible (toggle back on)
    expect(
      screen.getByText(/the skill stays installed/i, { exact: false }),
    ).toBeTruthy(); // the fine print
  });

  it("a toggle POSTs the session mute and re-renders from the response", async () => {
    const calls = stubFetch();
    renderSection();
    fireEvent.click(screen.getByTestId("access-toggle"));
    await screen.findByText("weekly-report");
    const toggles = screen
      .getAllByTitle("On for this session — tap to mute here");
    fireEvent.click(toggles[0]);
    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST" && c.url.includes("/skills"));
      expect(post?.body).toMatchObject({ skill: "weekly-report", enabled: false });
      expect(post?.url).toContain("/v1/sessions/s1/skills");
    });
  });

  it("Manage all skills → fires the two-doors callback", async () => {
    stubFetch();
    const onOpenSkills = vi.fn();
    renderSection({ onOpenSkills });
    fireEvent.click(screen.getByTestId("access-toggle"));
    fireEvent.click(await screen.findByText("Manage all skills →"));
    expect(onOpenSkills).toHaveBeenCalled();
  });

  it("shows a quiet empty state when no skills are installed", async () => {
    stubFetch({ skills: [] });
    renderSection();
    fireEvent.click(screen.getByTestId("access-toggle"));
    expect(await screen.findByText("No skills installed yet.")).toBeTruthy();
    // no skills fact in the glance when nothing is on
    expect(screen.getByTestId("access-summary").textContent).not.toContain("skill");
  });
});
