// Async artifact reads are session-owned: late work must never overwrite the active rail.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ArtifactContent, ArtifactInfo } from "../api";
import { getArtifacts, readArtifact } from "../api";
import { RightRail } from "./RightRail";

vi.mock("../api", () => ({
  getArtifacts: vi.fn(),
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
    fireEvent.click(await screen.findByText("first.md"));
    fireEvent.click(screen.getByLabelText("Back to artifacts"));
    fireEvent.click(screen.getByText("second.md"));

    await act(async () => secondRead.resolve(content("second.md", "Current content")));
    expect(screen.getByText("Current content")).toBeTruthy();

    await act(async () => firstRead.resolve(content("first.md", "Stale content")));
    expect(screen.getByText("Current content")).toBeTruthy();
    expect(screen.queryByText("Stale content")).toBeNull();
  });
});
