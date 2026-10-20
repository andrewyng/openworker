// The rail's preview notification must be edge-triggered: a new onPreviewChange
// identity (App re-renders whenever the nav toggles) must NOT replay "open" while
// the viewer sits open — that re-collapsed a sidebar the user had just expanded
// (owner-hit 2026-08-21).
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";
import { replayPlan } from "../api";
import { OPEN_ARTIFACT_EVENT } from "./Markdown";

vi.mock("../api", async () => {
  const actual: any = await vi.importActual("../api");
  return {
    ...actual,
    getArtifacts: vi.fn().mockResolvedValue([]),
    getRoots: vi.fn().mockResolvedValue([]),
    getJournalCases: vi.fn().mockResolvedValue([]),
    readArtifact: vi.fn().mockResolvedValue({ ok: true, path: "r.md", kind: "markdown", content: "x" }),
    replayPlan: vi.fn().mockResolvedValue({ session_id: "replayed", workspace: "/work", agent: "code" }),
    revealArtifact: vi.fn().mockResolvedValue({ ok: true }),
  };
});

afterEach(() => cleanup());

function rail(onPreviewChange: (open: boolean) => void) {
  return (
    <RightRail
      active
      sessionId="s1"
      refreshKey={0}
      toolNames={[]}
      todo={[]}
      running={false}
      onPreviewChange={onPreviewChange}
    />
  );
}

describe("RightRail preview notification", () => {
  it("fires only on open/close transitions, not on callback identity changes", async () => {
    const first = vi.fn();
    const { rerender } = render(rail(first));
    await act(async () => {});
    expect(first).not.toHaveBeenCalled(); // closed at mount: no "closed" replay either

    // Open the viewer via a transcript chip event.
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "r.md" } }),
      );
    });
    expect(first).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenLastCalledWith(true);

    // App re-renders with a NEW callback identity (e.g. the user expanded the nav).
    const second = vi.fn();
    rerender(rail(second));
    await act(async () => {});
    // The viewer never transitioned, so the new callback must not be told "open".
    expect(second).not.toHaveBeenCalled();
  });
});


it("replays the selected older plan artifact rather than the latest plan", async () => {
  vi.mocked(replayPlan).mockClear();
  render(rail(vi.fn()));
  await act(async () => {
    window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "plans/older-approved.md" } }));
  });
  fireEvent.click(await screen.findByTestId("artifact-rerun-plan"));
  await waitFor(() => expect(replayPlan).toHaveBeenCalledWith("s1", "older-approved"));
});

it("shows a replay failure to the user", async () => {
  vi.mocked(replayPlan).mockRejectedValueOnce(new Error("Selected plan is no longer approved"));
  render(rail(vi.fn()));
  await act(async () => {
    window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "plan.md" } }));
  });
  fireEvent.click(await screen.findByTestId("artifact-rerun-plan"));
  expect((await screen.findByRole("alert")).textContent).toContain("Selected plan is no longer approved");
});


it("uses the latest plan only for the root plan artifact", async () => {
  vi.mocked(replayPlan).mockClear();
  render(rail(vi.fn()));
  await act(async () => {
    window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "plan.md" } }));
  });
  fireEvent.click(await screen.findByTestId("artifact-rerun-plan"));
  await waitFor(() => expect(replayPlan).toHaveBeenCalledWith("s1", undefined));
});

it("does not offer replay for unrelated files named plan.md", async () => {
  render(rail(vi.fn()));
  await act(async () => {
    window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "notes/plan.md" } }));
  });
  expect(screen.queryByTestId("artifact-rerun-plan")).toBeNull();
});
