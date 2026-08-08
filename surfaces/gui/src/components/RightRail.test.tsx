import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RightRail } from "./RightRail";
import type { ArtifactInfo } from "../api";

const ARTIFACT: ArtifactInfo = {
  path: "test-doc.md",
  name: "test-doc.md",
  kind: "markdown",
  size: 100,
  modified_at: 1000,
};

function stubApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/artifacts/read")) {
        return {
          ok: true,
          json: async () => ({ kind: "markdown", content: "# Test Document" }),
        } as Response;
      }
      if (url.includes("/artifacts")) {
        return {
          ok: true,
          json: async () => ({ artifacts: [ARTIFACT] }),
        } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    })
  );
}

const defaultProps = {
  active: true,
  sessionId: "s1",
  refreshKey: 0,
  toolNames: [],
  todo: [],
  running: false,
  showArtifacts: true,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("RightRail artifact preview & navigation collapse sync (#135)", () => {
  it("notifies onPreviewChange when an artifact is selected and when rail is hidden", async () => {
    stubApi();
    const onPreviewChange = vi.fn();

    const { rerender } = render(
      <RightRail {...defaultProps} onPreviewChange={onPreviewChange} />
    );

    // Click on the artifact to select it
    const row = await screen.findByText("test-doc.md");
    fireEvent.click(row);

    await waitFor(() => {
      expect(onPreviewChange).toHaveBeenCalledWith(true);
    });

    // Hiding the side panel (active = false) must notify onPreviewChange(false)
    rerender(
      <RightRail {...defaultProps} active={false} onPreviewChange={onPreviewChange} />
    );

    await waitFor(() => {
      expect(onPreviewChange).toHaveBeenLastCalledWith(false);
    });
  });

  it("clears selected artifact when rail becomes inactive so reopening rail does not restore stale preview", async () => {
    stubApi();
    const onPreviewChange = vi.fn();

    const { rerender } = render(
      <RightRail {...defaultProps} onPreviewChange={onPreviewChange} />
    );

    const row = await screen.findByText("test-doc.md");
    fireEvent.click(row);

    await waitFor(() => {
      expect(screen.getByText("Artifacts")).toBeTruthy();
      expect(onPreviewChange).toHaveBeenLastCalledWith(true);
    });

    // Hide rail
    rerender(
      <RightRail {...defaultProps} active={false} onPreviewChange={onPreviewChange} />
    );

    await waitFor(() => {
      expect(onPreviewChange).toHaveBeenLastCalledWith(false);
    });

    // Reopen rail (active = true)
    rerender(
      <RightRail {...defaultProps} active={true} onPreviewChange={onPreviewChange} />
    );

    // Should return to artifact list, not artifact viewer, and preview state remains false
    await waitFor(() => {
      expect(screen.getByText("test-doc.md")).toBeTruthy();
      expect(onPreviewChange).toHaveBeenLastCalledWith(false);
    });
  });

  it("does not re-emit preview true when onPreviewChange callback identity changes while inactive", async () => {
    stubApi();
    const onPreviewChange1 = vi.fn();
    const onPreviewChange2 = vi.fn();

    const { rerender } = render(
      <RightRail {...defaultProps} onPreviewChange={onPreviewChange1} />
    );

    const row = await screen.findByText("test-doc.md");
    fireEvent.click(row);

    await waitFor(() => {
      expect(onPreviewChange1).toHaveBeenCalledWith(true);
    });

    // Hide rail
    rerender(
      <RightRail {...defaultProps} active={false} onPreviewChange={onPreviewChange1} />
    );

    // Change callback identity
    rerender(
      <RightRail {...defaultProps} active={false} onPreviewChange={onPreviewChange2} />
    );

    expect(onPreviewChange2).not.toHaveBeenCalledWith(true);
  });

  it("closing artifact preview with Back button calls onPreviewChange(false)", async () => {
    stubApi();
    const onPreviewChange = vi.fn();

    render(<RightRail {...defaultProps} onPreviewChange={onPreviewChange} />);

    const row = await screen.findByText("test-doc.md");
    fireEvent.click(row);

    await waitFor(() => {
      expect(onPreviewChange).toHaveBeenLastCalledWith(true);
    });

    const backBtn = await screen.findByTitle("Back");
    fireEvent.click(backBtn);

    await waitFor(() => {
      expect(onPreviewChange).toHaveBeenLastCalledWith(false);
    });
  });
});
