// Async artifact reads are session-owned: late work must never overwrite the active rail.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ArtifactContent, ArtifactInfo, AgentTaskInfo, JournalCase, RootInfo } from "../api";
import { getAgentTasks, getArtifacts, getJournalCases, getRoots, readArtifact } from "../api";
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

const root = (path: string): RootInfo => ({
  path,
  writable: true,
  label: path,
  primary: false,
  exists: true,
});

const agentTask = (id: string): AgentTaskInfo => ({
  id,
  label: id,
  task: id,
  mode: "read_only",
  status: "done",
  created: 1,
  icon_seed: 1,
});

const journalCase = (name: string): JournalCase => ({
  case: name,
  entries: 1,
  last_ts: "2026-11-21T00:00:00Z",
});

// A board is what makes the rail ask for journal cases at all.
const board = { space: "space-1", name: "Board", items: [] };

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

// The roots / subagent / journal lists are session-owned too. Only the artifact list
// carried a request-id guard; a slow session must not paint its rows over a fast one.
describe("RightRail session-scoped list ownership", () => {
  it("ignores a roots response from the previous session", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([]);
    const oldRoots = deferred<RootInfo[]>();
    const currentRoots = deferred<RootInfo[]>();
    vi.mocked(getRoots)
      .mockReturnValueOnce(oldRoots.promise)
      .mockReturnValueOnce(currentRoots.promise);

    const { rerender } = render(<RightRail {...baseProps} />);
    rerender(<RightRail {...baseProps} sessionId="session-b" />);

    await act(async () => currentRoots.resolve([root("b-folder")]));
    expect(screen.getByTestId("rail-toggle-files").textContent).toContain("1");
    const shown = screen.getByTestId("rail-toggle-files").textContent;

    await act(async () => oldRoots.resolve([root("a-folder"), root("a-other")]));
    expect(screen.getByTestId("rail-toggle-files").textContent).toBe(shown);
    expect(screen.queryByTitle("a-folder")).toBeNull();
  });

  it("ignores an agent-task response from the previous session", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([]);
    const oldTasks = deferred<AgentTaskInfo[]>();
    const currentTasks = deferred<AgentTaskInfo[]>();
    vi.mocked(getAgentTasks)
      .mockReturnValueOnce(oldTasks.promise)
      .mockReturnValueOnce(currentTasks.promise);

    const { rerender } = render(<RightRail {...baseProps} />);
    rerender(<RightRail {...baseProps} sessionId="session-b" />);

    await act(async () => currentTasks.resolve([agentTask("b-task")]));
    const head = screen.getByTestId("rail-toggle-subagents");
    const shown = head.textContent;

    await act(async () => oldTasks.resolve([agentTask("a-task"), agentTask("a-other-task")]));
    expect(screen.getByTestId("rail-toggle-subagents").textContent).toBe(shown);
    expect(screen.queryByTitle("a-task")).toBeNull();
  });

  it("ignores a journal response from the previous session", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([]);
    const oldCases = deferred<JournalCase[]>();
    const currentCases = deferred<JournalCase[]>();
    vi.mocked(getJournalCases)
      .mockReturnValueOnce(oldCases.promise)
      .mockReturnValueOnce(currentCases.promise);

    const { rerender } = render(<RightRail {...baseProps} board={board} />);
    rerender(<RightRail {...baseProps} sessionId="session-b" board={board} />);

    await act(async () => currentCases.resolve([journalCase("b-case")]));
    expect(screen.getByTestId("rail-toggle-journal").textContent).toContain("1");
    const shown = screen.getByTestId("rail-toggle-journal").textContent;

    await act(async () => oldCases.resolve([journalCase("a-case"), journalCase("a-other")]));
    expect(screen.getByTestId("rail-toggle-journal").textContent).toBe(shown);
    expect(screen.queryByText("a-case")).toBeNull();
  });
});
