import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Markdown, OPEN_ARTIFACT_EVENT, normalizeArtifactPath } from "./Markdown";

afterEach(cleanup);

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

  it("chip title falls back to the filename when the link text is empty", () => {
    vi.spyOn(window, "dispatchEvent");
    render(<Markdown text="[](artifact:out/report.pdf)" />);
    expect(screen.getByTestId("artifact-chip").textContent).toContain("report.pdf");
  });

  // Chinese (and other non-ASCII) filenames: micromark percent-encodes the href; without
  // decode the backend looks up the literal %E6… path and returns "not found".
  it("decodes percent-encoded non-ASCII artifact paths before opening", () => {
    const seen: string[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent).detail.path);
    window.addEventListener(OPEN_ARTIFACT_EVENT, listener);

    render(<Markdown text="📄 [Monthly report](artifact:reports/2026-04_月度报告.md)" />);
    const chip = screen.getByTestId("artifact-chip");
    expect(chip.getAttribute("title")).toBe("reports/2026-04_月度报告.md");
    fireEvent.click(chip);
    expect(seen).toEqual(["reports/2026-04_月度报告.md"]);

    window.removeEventListener(OPEN_ARTIFACT_EVENT, listener);
  });
});

describe("normalizeArtifactPath", () => {
  it("decodes percent-encoded segments and strips a leading slash", () => {
    expect(normalizeArtifactPath("reports/2026-04_%E6%9C%88%E5%BA%A6%E6%8A%A5%E5%91%8A.md")).toBe(
      "reports/2026-04_月度报告.md",
    );
    expect(normalizeArtifactPath("/reports/foo.md")).toBe("reports/foo.md");
    expect(normalizeArtifactPath("reports/ascii.md")).toBe("reports/ascii.md");
  });
});
