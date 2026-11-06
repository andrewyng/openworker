import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { OPEN_ARTIFACT_EVENT } from "./Markdown";
import { MarkdownFileView, splitMarkdownFrontmatter } from "./MarkdownFileView";
import { RightRail } from "./RightRail";

afterEach(cleanup);

const FOUNDER = `---
id: founder-sales
name: Founder Sales
icon: target
tagline: Your one-to-one founder-led sales partner — research, stage, and learn without sending for you
subagents: false
scheduling: true
version: 1
tools: [search, todo]
connectors: [gmail, google_calendar, hubspot, attio, slack, stripe]
default_permission_mode: interactive
description: A solo founder-led sales coworker that turns ICP research into a daily action list.
recommends:
  - connector: gmail
    reason: research relationship history
    tier: core
---

You are Founder Sales, the user's one-to-one, founder-led sales coworker.

## First-use setup

Before running sales routines, conduct a structured setup interview.
`;

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    getArtifacts: vi.fn().mockResolvedValue([]),
    getRoots: vi.fn().mockResolvedValue([]),
    getJournalCases: vi.fn().mockResolvedValue([]),
    readArtifact: vi.fn().mockImplementation(async () => ({
      ok: true,
      path: "manifest.md",
      kind: "markdown",
      content: `---
id: founder-sales
name: Founder Sales
icon: target
tagline: Your one-to-one founder-led sales partner — research, stage, and learn without sending for you
subagents: false
scheduling: true
version: 1
tools: [search, todo]
connectors: [gmail, google_calendar, hubspot, attio, slack, stripe]
default_permission_mode: interactive
description: A solo founder-led sales coworker that turns ICP research into a daily action list.
recommends:
  - connector: gmail
    reason: research relationship history
    tier: core
---

You are Founder Sales, the user's one-to-one, founder-led sales coworker.

## First-use setup

Before running sales routines, conduct a structured setup interview.
`,
    })),
    revealArtifact: vi.fn().mockResolvedValue({ ok: true }),
  };
});

describe("splitMarkdownFrontmatter", () => {
  it("keeps a file with no fence as the body", () => {
    const text = "# Notes\n\nhello from the explorer\n\n---\n\nafter the rule";
    expect(splitMarkdownFrontmatter(text)).toEqual({ meta: null, body: text });
  });

  it("splits scalars, booleans, lists, and nested maps, and leaves a later rule in the body", () => {
    const { meta, body } = splitMarkdownFrontmatter(FOUNDER);
    expect(meta).toMatchObject({
      id: "founder-sales",
      name: "Founder Sales",
      subagents: false,
      scheduling: true,
      version: 1,
      tools: ["search", "todo"],
    });
    expect(Array.isArray(meta?.recommends)).toBe(true);
    expect(body).toContain("## First-use setup");
    expect(body.startsWith("---")).toBe(false);
  });

  it("treats an empty frontmatter block as an empty mapping", () => {
    expect(splitMarkdownFrontmatter("---\n---\n\nJust the body.\n")).toEqual({
      meta: {},
      body: "Just the body.\n",
    });
  });

  it("falls back to the whole file when the YAML is invalid or not a mapping", () => {
    const invalid = "---\n: [unterminated\n---\n\nHello\n";
    expect(splitMarkdownFrontmatter(invalid)).toEqual({ meta: null, body: invalid });
    const list = "---\n- just\n- a\n- list\n---\n\nHello\n";
    expect(splitMarkdownFrontmatter(list)).toEqual({ meta: null, body: list });
  });

  it("does not treat a longer dash line as the closer", () => {
    const text = "---\nid: kept\n----: marker\nstill: inside\n---\n\nBody\n";
    const { meta, body } = splitMarkdownFrontmatter(text);
    expect(meta).toMatchObject({ id: "kept", "----": "marker", still: "inside" });
    expect(body).toBe("Body\n");
  });
});

describe("MarkdownFileView", () => {
  it("renders a founder-sales manifest as properties plus the body", () => {
    const { container } = render(<MarkdownFileView text={FOUNDER} mode="preview" />);
    expect(screen.getByTestId("markdown-properties")).toBeTruthy();
    expect(screen.getByText("founder-sales")).toBeTruthy();
    expect(screen.getByText("Founder Sales")).toBeTruthy();
    expect(screen.getByText(/without sending for you/)).toBeTruthy();
    const scheduling = screen.getByRole("checkbox", { name: "scheduling" }) as HTMLInputElement;
    const subagents = screen.getByRole("checkbox", { name: "subagents" }) as HTMLInputElement;
    expect(scheduling.checked).toBe(true);
    expect(scheduling.disabled).toBe(true);
    expect(subagents.checked).toBe(false);
    expect(subagents.disabled).toBe(true);
    for (const chip of ["search", "todo", "gmail", "stripe"]) {
      expect(screen.getByText(chip).className).toContain("md-prop-chip");
    }
    expect(screen.getByRole("heading", { name: "First-use setup" })).toBeTruthy();
    expect(screen.queryByText(/id:\s*founder-sales/)).toBeNull();

    expect(container.querySelector(".md-prop-nested pre")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "recommends" }));
    expect(container.querySelector(".md-prop-nested pre")?.textContent).toContain("research relationship history");
  });

  it("renders plain markdown with no properties block", () => {
    const text = "# Notes\n\nhello from the explorer\n\n---\n\nafter the rule";
    const { container } = render(<MarkdownFileView text={text} mode="preview" />);
    expect(screen.queryByTestId("markdown-properties")).toBeNull();
    expect(container.querySelector(".artifact-md")?.textContent).toContain("hello from the explorer");
    expect(container.querySelector(".artifact-md")?.textContent).toContain("after the rule");
  });

  it("renders invalid YAML as the whole file", () => {
    const text = "---\n: [unterminated\n---\n\nHello from the bad file\n";
    render(<MarkdownFileView text={text} mode="preview" />);
    expect(screen.queryByTestId("markdown-properties")).toBeNull();
    expect(screen.getByText(/Hello from the bad file/)).toBeTruthy();
  });

  it("shows the raw file in source mode", () => {
    render(<MarkdownFileView text={FOUNDER} mode="source" />);
    expect(screen.getByTestId("markdown-source").textContent).toContain("id: founder-sales");
    expect(screen.queryByTestId("markdown-properties")).toBeNull();
  });
});

describe("markdown corpus", () => {
  const groups = [
    import.meta.glob("../../../../coworker/**/*.md", { query: "?raw", import: "default", eager: true }),
    import.meta.glob("../../../../surfaces/gui/e2e-live/**/*.md", { query: "?raw", import: "default", eager: true }),
    import.meta.glob("../../../../docs/**/*.md", { query: "?raw", import: "default", eager: true }),
  ];
  const files = groups.flatMap((group) =>
    Object.entries(group).filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].startsWith("---")),
  );

  it("previews every frontmatter markdown file in the repo", () => {
    expect(files.length).toBeGreaterThanOrEqual(20);
    for (const [path, text] of files) {
      cleanup();
      const { meta, body } = splitMarkdownFrontmatter(text);
      const view = render(<MarkdownFileView text={text} mode="preview" />);
      expect(meta, path).not.toBeNull();
      const shown = view.container.textContent ?? "";
      const firstField = text.split(/\r?\n/).slice(1).find((line) => line.includes(":"));
      if (firstField) expect(shown, path).not.toContain(firstField);
      for (const key of Object.keys(meta ?? {})) {
        expect(screen.getAllByText(key).length, `${path} key ${key}`).toBeGreaterThan(0);
      }
      const slice = body
        .split(/\r?\n/)
        .map((line) => line.trim().replace(/^#{1,6}\s+/, "").replace(/^[-*]\s+/, "").replace(/^\d+\.\s+/, ""))
        .find((line) => line.length >= 12);
      if (slice) expect(shown, path).toContain(slice.slice(0, 48));
      view.unmount();
    }
  });
});

describe("artifact viewer markdown switch", () => {
  it("opens on Preview and toggles to the raw source", async () => {
    render(
      <RightRail
        active
        sessionId="s1"
        refreshKey={0}
        toolNames={[]}
        todo={[]}
        running={false}
      />,
    );
    fireEvent(
      window,
      new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "manifest.md" } }),
    );
    expect((await screen.findByTestId("markdown-preview-tab")).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("markdown-properties")).toBeTruthy();
    expect(screen.queryByText(/id:\s*founder-sales/)).toBeNull();

    fireEvent.click(screen.getByTestId("markdown-source-tab"));
    expect(screen.getByTestId("markdown-source").textContent).toContain("id: founder-sales");
    expect(screen.queryByTestId("markdown-properties")).toBeNull();

    fireEvent.click(screen.getByTestId("markdown-preview-tab"));
    expect(screen.getByTestId("markdown-properties")).toBeTruthy();
    expect(screen.queryByTestId("markdown-source")).toBeNull();
  });
});
