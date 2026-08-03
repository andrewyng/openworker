// §16 workspace gate — "New project" mode must auto-open the OS folder picker (owner bug
// report 2026-08-03: it only revealed a blank form and never asked for a folder). The
// picked folder backfills the path and a default project name (folder basename); the user
// can still rename before Create.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FolderGate } from "./FolderGate";

const { chooseFolderMock } = vi.hoisted(() => ({ chooseFolderMock: vi.fn() }));

vi.mock("../tauri", () => ({
  chooseFolder: (...args: unknown[]) => chooseFolderMock(...args),
}));

function renderGate() {
  return render(<FolderGate onChoose={() => {}} projectIndex={[]} />);
}

describe("FolderGate", () => {
  afterEach(() => {
    cleanup();
    chooseFolderMock.mockReset();
  });

  it("auto-opens the folder picker when 'New project' is chosen and backfills", async () => {
    chooseFolderMock.mockResolvedValue("/tmp/proj-x");
    renderGate();

    fireEvent.click(screen.getByText("New project"));

    await waitFor(() => expect(chooseFolderMock).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        (screen.getByPlaceholderText("/path/to/your/project") as HTMLInputElement).value,
      ).toBe("/tmp/proj-x"),
    );
    expect((screen.getByPlaceholderText("Project name") as HTMLInputElement).value).toBe("proj-x");
  });

  it("does not open the picker for a plain session", async () => {
    renderGate();
    expect(chooseFolderMock).not.toHaveBeenCalled();
  });
});
