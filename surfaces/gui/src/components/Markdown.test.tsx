import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Markdown, OPEN_ARTIFACT_EVENT, OPEN_BOARD_EVENT } from "./Markdown";
import { openExternal } from "../tauri";

afterEach(cleanup);

vi.mock("../tauri", () => ({ openExternal: vi.fn() }));

beforeEach(() => {
  vi.mocked(openExternal).mockClear();
});

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

  // #607: in the desktop Tauri webview a plain target="_blank" never opens the OS browser, so a
  // left-click on an external link must be routed through openExternal() (which uses the opener
  // plugin in the packaged app and window.open in the browser).
  it("routes a left-click on an external link through openExternal and prevents default", () => {
    render(<Markdown text="see [the docs](https://example.com)" />);
    const a = screen.getByText("the docs").closest("a")!;
    fireEvent.click(a, { defaultPrevented: false });
    expect(openExternal).toHaveBeenCalledTimes(1);
    expect(openExternal).toHaveBeenCalledWith("https://example.com");
  });

  it("does not route artifact: or board: links through openExternal", () => {
    render(
      <Markdown text="see [the docs](https://example.com) and [file](artifact:reports/a.pdf)" />,
    );
    fireEvent.click(screen.getByTestId("artifact-chip"));
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("chip title falls back to the filename when the link text is empty", () => {
    vi.spyOn(window, "dispatchEvent");
    render(<Markdown text="[](artifact:out/report.pdf)" />);
    expect(screen.getByTestId("artifact-chip").textContent).toContain("report.pdf");
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
