// The Worktrees settings card (Code coworker, 2026-11-07): renders the saved values and
// sends one change with every field.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return {
    ...real,
    getSettings: vi.fn().mockResolvedValue({
      provider: "openai", model: "m", models: ["m"], scratch_base: "/tmp/s",
      worktree_location: "sibling", worktree_branch_prefix: "rp/", worktree_prune_days: 7,
      worktree_fetch_first: true, worktree_cleanup_on_archive: false, worktree_setup_command: "npm install",
    }),
    setWorktreeSettings: vi.fn().mockResolvedValue({ ok: true }),
  };
});

vi.mock("../tauri", () => ({ isTauri: () => false, pickFolder: vi.fn() }));

afterEach(cleanup);

describe("WorktreesCard", () => {
  it("shows the saved settings and saves the whole draft", async () => {
    const { SettingsView } = await import("./SettingsView");
    const api = await import("../api");
    render(<SettingsView initialTab="appearance" />);
    const card = await screen.findByTestId("worktrees-card");
    expect((card.querySelector('[data-testid="worktree-location"]') as HTMLSelectElement).value).toBe("sibling");
    expect(card.textContent).toContain("Feature worktrees");

    fireEvent.change(card.querySelector('[data-testid="worktree-location"]')!, { target: { value: "in_repo" } });
    fireEvent.click(screen.getByTestId("worktrees-save"));
    await waitFor(() => expect(api.setWorktreeSettings).toHaveBeenCalled());
    const sent = (api.setWorktreeSettings as any).mock.calls[0][0];
    expect(sent.location).toBe("in_repo");
    expect(sent.branch_prefix).toBe("rp/");
    expect(sent.prune_days).toBe(7);
    expect(sent.cleanup_on_archive).toBe(false);
    expect(sent.setup_command).toBe("npm install");
  });
});
