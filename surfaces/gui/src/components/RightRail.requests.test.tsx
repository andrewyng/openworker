// Async artifact reads are session-owned: late work must never overwrite the active rail.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ArtifactContent, ArtifactInfo } from "../api";
import { getArtifacts, getRoots, readArtifact } from "../api";
import { RightRail } from "./RightRail";

vi.mock("../api", () => ({
  getAgentTasks: vi.fn().mockResolvedValue([]),
  getArtifacts: vi.fn(),
  getJournalCases: vi.fn().mockResolvedValue([]),
  getRoots: vi.fn().mockResolvedValue([]),
  readArtifact: vi.fn(),
  revealArtifact: vi.fn(),
}));

vi.mock("./AccessSection", () => ({ AccessSection: () => null }));

const artifact = (name: string): ArtifactInfo => ({
  path: name,
  name,
  kind: "markdown",
  size: 10,
  modified_at: 1,
});

const content = (path: string, body: string): ArtifactContent => ({
  ok: true,
  path,
  kind: "markdown",
  content: body,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const baseProps = {
  active: true,
  sessionId: "session-a",
  refreshKey: 0,
  toolNames: [],
  todo: [],
  running: false,
};

function openArtifacts() {
  fireEvent.click(screen.getByTestId("rail-toggle-artifacts"));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RightRail artifact request ownership", () => {
  it("clears the previous session's artifacts while the current list is loading", async () => {
    const currentRequest = deferred<ArtifactInfo[]>();
    vi.mocked(getArtifacts)
      .mockResolvedValueOnce([artifact("previous.md")])
      .mockReturnValueOnce(currentRequest.promise);

    const { rerender } = render(<RightRail {...baseProps} />);
    openArtifacts();
    expect(await screen.findByText("previous.md")).toBeTruthy();

    rerender(<RightRail {...baseProps} sessionId="session-b" />);
    expect(screen.queryByText("previous.md")).toBeNull();

    await act(async () => currentRequest.resolve([artifact("current.md")]));
    expect(screen.getByText("current.md")).toBeTruthy();
  });

  it("ignores an artifact-list response from the previous session", async () => {
    const oldRequest = deferred<ArtifactInfo[]>();
    const currentRequest = deferred<ArtifactInfo[]>();
    vi.mocked(getArtifacts)
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(currentRequest.promise);

    const { rerender } = render(<RightRail {...baseProps} />);
    openArtifacts();
    rerender(<RightRail {...baseProps} sessionId="session-b" />);

    await act(async () => currentRequest.resolve([artifact("current.md")]));
    expect(screen.getByText("current.md")).toBeTruthy();

    await act(async () => oldRequest.resolve([artifact("stale.md")]));
    expect(screen.getByText("current.md")).toBeTruthy();
    expect(screen.queryByText("stale.md")).toBeNull();
  });

  it("ignores a rejected artifact-list request after a newer request succeeds", async () => {
    const oldRequest = deferred<ArtifactInfo[]>();
    const currentRequest = deferred<ArtifactInfo[]>();
    vi.mocked(getArtifacts)
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(currentRequest.promise);

    const { rerender } = render(<RightRail {...baseProps} />);
    openArtifacts();
    rerender(<RightRail {...baseProps} sessionId="session-b" />);

    await act(async () => currentRequest.resolve([artifact("current.md")]));
    await act(async () => oldRequest.reject(new Error("stale failure")));

    expect(screen.getByText("current.md")).toBeTruthy();
  });

  it("ignores file content that finishes after the user selects another artifact", async () => {
    const firstRead = deferred<ArtifactContent>();
    const secondRead = deferred<ArtifactContent>();
    vi.mocked(getArtifacts).mockResolvedValue([artifact("first.md"), artifact("second.md")]);
    vi.mocked(readArtifact).mockImplementation((_sessionId, path) =>
      path === "first.md" ? firstRead.promise : secondRead.promise,
    );

    render(<RightRail {...baseProps} />);
    openArtifacts();
    fireEvent.click(await screen.findByText("first.md"));
    fireEvent.click(screen.getByTestId("artifact-crumb-back"));
    fireEvent.click(screen.getByText("second.md"));

    await act(async () => secondRead.resolve(content("second.md", "Current content")));
    expect(screen.getByText("Current content")).toBeTruthy();

    await act(async () => firstRead.resolve(content("first.md", "Stale content")));
    expect(screen.getByText("Current content")).toBeTruthy();
    expect(screen.queryByText("Stale content")).toBeNull();
  });
});

// -- roots freshness (#490): a folder added mid-session must reach the rail --

const root = (path: string, label: string, primary = false) => ({
  path,
  writable: true,
  label,
  primary,
  exists: true,
});

describe("RightRail roots freshness", () => {
  it("refetches roots when the session's roots change, so a newly added folder appears", async () => {
    vi.mocked(getRoots)
      .mockResolvedValueOnce([root("/tmp/scratch", "scratch", true)])
      .mockResolvedValueOnce([
        root("/tmp/scratch", "scratch", true),
        root("/Users/me/Reports", "Reports"),
      ]);

    render(<RightRail {...baseProps} />);
    const filesToggle = await screen.findByTestId("rail-toggle-files");
    expect(filesToggle.textContent).toContain("1");

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("coworker:roots-changed", { detail: "session-a" }),
      );
    });

    // The collapsed header's count bumps without a session switch, and the
    // added folder is browsable once expanded.
    expect(screen.getByTestId("rail-toggle-files").textContent).toContain("2");
    fireEvent.click(screen.getByTestId("rail-toggle-files"));
    expect(await screen.findByText("Reports")).toBeTruthy();
  });

  it("ignores a roots-changed event for a different session", async () => {
    vi.mocked(getRoots).mockResolvedValueOnce([root("/tmp/scratch", "scratch", true)]);

    render(<RightRail {...baseProps} />);
    expect(await screen.findByTestId("rail-toggle-files")).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("coworker:roots-changed", { detail: "session-b" }),
      );
    });

    expect(vi.mocked(getRoots)).toHaveBeenCalledTimes(1);
  });
});
