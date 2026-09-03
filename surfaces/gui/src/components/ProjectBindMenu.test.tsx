// UX-044: label trimming + submenu rendering rules.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ProjectBindMenu, trimPath } from "./ProjectBindMenu";

const menuPayload = {
  kind: "memory",
  bound: null,
  derived: { kind: "folder", label: "~/a/b/c/d/notes", full: "/u/a/b/c/d/notes", key: "/u/a/b/c/d/notes" },
  named: [
    { name: "openworker", key: "/k1" },
    { name: "personal-ops", key: "/k2" },
  ],
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ json: async () => menuPayload }) as Response),
  );
});

describe("trimPath", () => {
  it("keeps short paths whole", () => {
    expect(trimPath("~/projects/api")).toBe("~/projects/api");
  });
  it("trims long paths to the last 3 segments", () => {
    expect(trimPath("~/fleet/ro4d/demo-universe/notes")).toBe("…/ro4d/demo-universe/notes");
  });
});

describe("ProjectBindMenu", () => {
  it("renders derived (trimmed) + named rows, no filter under 6", async () => {
    render(
      <ProjectBindMenu sessionId="s1" kind="memory" onClose={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText("openworker")).toBeTruthy());
    expect(screen.getByText("…/c/d/notes")).toBeTruthy();
    expect(screen.getByText("this folder")).toBeTruthy();
    expect(screen.queryByPlaceholderText("Filter…")).toBeNull();
    expect(screen.getByText(/Name current memory/)).toBeTruthy();
  });

  it("shows the filter at 6+ named and filters in place", async () => {
    const big = {
      ...menuPayload,
      named: Array.from({ length: 7 }, (_, i) => ({ name: `mem-${i}`, key: `/k${i}` })),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ json: async () => big }) as Response),
    );
    render(<ProjectBindMenu sessionId="s1" kind="memory" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByPlaceholderText("Filter…")).toBeTruthy());
    // MRU cap: only 5 named rows visible unfiltered
    expect(screen.queryByText("mem-5")).toBeNull();
  });

  it("board gets a none row; memory does not", async () => {
    render(<ProjectBindMenu sessionId="s1" kind="board" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("none")).toBeTruthy());
  });
});
