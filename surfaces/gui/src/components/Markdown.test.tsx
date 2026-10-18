import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Markdown, OPEN_ARTIFACT_EVENT, OPEN_BOARD_EVENT } from "./Markdown";
import { openExternal } from "../tauri";

vi.mock("../tauri", () => ({ openExternal: vi.fn() }));

afterEach(cleanup);
afterEach(() => vi.mocked(openExternal).mockReset());

// §34 (UX-016): [Title](artifact:path) renders as a chip that opens the artifact viewer via
// a window event; ordinary links keep the open-externally treatment.
describe("Markdown artifact links", () => {
  it("renders an artifact: link as a chip and dispatches the open event with the path", () => {
    const seen: string[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent).detail.path);
    window.addEventListener(OPEN_ARTIFACT_EVENT, listener);

    render(<Markdown text="Done — [Semiconductor dashboard](artifact:reports/semi.html)" />);
    const chip = screen.getByTestId("artifact-chip");
    expect(chip.textContent).toContain("Semiconductor dashboard");
    expect(chip.textContent).toContain("semi.html"); // filename shown under the title
    fireEvent.click(chip);
    expect(seen).toEqual(["reports/semi.html"]);

    window.removeEventListener(OPEN_ARTIFACT_EVENT, listener);
  });

  it("ordinary links stay external and never become chips", () => {
    const { container } = render(<Markdown text="see [the docs](https://example.com)" />);
    expect(screen.queryByTestId("artifact-chip")).toBeNull();
    const a = container.querySelector("a")!;
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("href")).toBe("https://example.com");
  });

  // #607: the desktop webview intercepts target=_blank and silently no-ops, so a link
  // click must go through the opener (openExternal) — the webview's own navigation
  // never runs. In a browser build openExternal IS window.open, so dev keeps its tab.
  it("an ordinary link click rides the opener, never the webview's own navigation", () => {
    const { container } = render(<Markdown text="see [the docs](https://example.com)" />);
    const a = container.querySelector("a")!;
    // fireEvent returns !defaultPrevented: false = the anchor's own navigation never ran
    expect(fireEvent.click(a)).toBe(false);
    expect(openExternal).toHaveBeenCalledTimes(1);
    expect(openExternal).toHaveBeenCalledWith("https://example.com");
  });

  it("chip clicks never touch the opener", () => {
    render(<Markdown text="[Board · 5 items](board:)" />);
    fireEvent.click(screen.getByTestId("board-chip"));
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("chip title falls back to the filename when the link text is empty", () => {
    vi.spyOn(window, "dispatchEvent");
    render(<Markdown text="[](artifact:out/report.pdf)" />);
    expect(screen.getByTestId("artifact-chip").textContent).toContain("report.pdf");
  });

  it("dispatches a decoded Unicode filename", () => {
    const seen: string[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent).detail.path);
    window.addEventListener(OPEN_ARTIFACT_EVENT, listener);

    render(<Markdown text="[News](artifact:%E6%96%B0%E9%97%BB%E6%91%98%E8%A6%81.md)" />);
    fireEvent.click(screen.getByTestId("artifact-chip"));
    expect(seen).toEqual(["新闻摘要.md"]);

    window.removeEventListener(OPEN_ARTIFACT_EVENT, listener);
  });

  // Seventeenth pass: the lead's one-time board mention — [Board · 5 items](board:)
  // renders as an inline pill that opens the drawer on its Board section.
  it("renders a board: link as a pill and dispatches the open-board event", () => {
    let fired = 0;
    const listener = () => fired++;
    window.addEventListener(OPEN_BOARD_EVENT, listener);

    render(<Markdown text="Plan approved — [Board · 5 items](board:) if you want to watch." />);
    const chip = screen.getByTestId("board-chip");
    expect(chip.textContent).toContain("Board · 5 items");
    fireEvent.click(chip);
    expect(fired).toBe(1);

    window.removeEventListener(OPEN_BOARD_EVENT, listener);
  });
});
