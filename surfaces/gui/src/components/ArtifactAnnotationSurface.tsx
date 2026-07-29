import html2canvas from "html2canvas";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type {
  AnnotationRect,
  AnnotationTarget,
  ArtifactAnnotation,
} from "../types";
import { Markdown } from "./Markdown";

export interface CapturedSelection {
  target: AnnotationTarget;
  preview: ArtifactAnnotation["preview"];
  anchor: DOMRect;
}

interface SurfaceProps {
  kind: "pdf" | "image" | "markdown" | "html";
  dataUrl?: string;
  content?: string;
  annotating: boolean;
  focusAnnotation?: ArtifactAnnotation | null;
  stagedAnnotations?: ArtifactAnnotation[];
  draftTarget?: AnnotationTarget;
  onSelection: (selection: CapturedSelection) => void;
  reloadKey?: number;
}

interface LocalRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

const BLOCK_SELECTOR =
  "p,h1,h2,h3,h4,h5,h6,li,pre,blockquote,table,figure,img,section,article,header,footer,nav,button,a";
const SELECTION_PAD_X = 2.3409;
const SELECTION_PAD_TOP = 2.85;
const SELECTION_PAD_BOTTOM = 3.993;
const SELECTION_GROWTH_X_PERCENT = 5.85225;
const SELECTION_GROWTH_TOP_PERCENT = 9.5;
// Percentage growth is relative to the captured selection's own dimensions.
// Bottom uses an additive adjustment so a requested +20% is visually meaningful.
const SELECTION_GROWTH_BOTTOM_PERCENT = 33.31;
const SELECTION_VISUAL_INSET =
  `calc(-${SELECTION_PAD_TOP}px - ${SELECTION_GROWTH_TOP_PERCENT}%) `
  + `calc(-${SELECTION_PAD_X}px - ${SELECTION_GROWTH_X_PERCENT}%) `
  + `calc(-${SELECTION_PAD_BOTTOM}px - ${SELECTION_GROWTH_BOTTOM_PERCENT}%) `
  + `calc(-${SELECTION_PAD_X}px - ${SELECTION_GROWTH_X_PERCENT}%)`;

function normalizedRect(rect: LocalRect, width: number, height: number): AnnotationRect {
  const safeWidth = Math.max(1, width);
  const safeHeight = Math.max(1, height);
  return {
    x: Math.max(0, Math.min(1, rect.x / safeWidth)),
    y: Math.max(0, Math.min(1, rect.y / safeHeight)),
    width: Math.max(0.0001, Math.min(1, rect.width / safeWidth)),
    height: Math.max(0.0001, Math.min(1, rect.height / safeHeight)),
  };
}

function clientRectFor(root: HTMLElement, rect: LocalRect): DOMRect {
  const bounds = root.getBoundingClientRect();
  return new DOMRect(
    bounds.left + rect.x,
    bounds.top + rect.y,
    rect.width,
    rect.height,
  );
}

function cropCanvas(
  source: HTMLCanvasElement,
  rect: LocalRect,
  sourceCssWidth: number,
  sourceCssHeight: number,
): ArtifactAnnotation["preview"] {
  const scaleX = source.width / Math.max(1, sourceCssWidth);
  const scaleY = source.height / Math.max(1, sourceCssHeight);
  const sx = Math.max(0, Math.floor(rect.x * scaleX));
  const sy = Math.max(0, Math.floor(rect.y * scaleY));
  const sw = Math.max(1, Math.min(source.width - sx, Math.ceil(rect.width * scaleX)));
  const sh = Math.max(1, Math.min(source.height - sy, Math.ceil(rect.height * scaleY)));
  const maxSide = 1400;
  const outputScale = Math.min(1, maxSide / Math.max(sw, sh));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(sw * outputScale));
  canvas.height = Math.max(1, Math.round(sh * outputScale));
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Could not capture this selection.");
  context.drawImage(source, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  return {
    data_url: canvas.toDataURL("image/png"),
    width: canvas.width,
    height: canvas.height,
  };
}

async function captureDom(root: HTMLElement, rect: LocalRect) {
  const canvas = await html2canvas(root, {
    backgroundColor: "#ffffff",
    foreignObjectRendering: true,
    logging: false,
    scale: 1,
    useCORS: true,
    ignoreElements: (element) =>
      element.classList.contains("artifact-annotation-overlay")
      || element.classList.contains("annotation-focus-box"),
  });
  return cropCanvas(canvas, rect, root.scrollWidth, root.scrollHeight);
}

function cssPath(element: Element, root: Element): string {
  const parts: string[] = [];
  let current: Element | null = element;
  while (current && current !== root && parts.length < 8) {
    let part = current.tagName.toLowerCase();
    if (current.id) {
      part += `#${CSS.escape(current.id)}`;
      parts.unshift(part);
      break;
    }
    const parentElement: Element | null = current.parentElement;
    if (parentElement) {
      const peers: Element[] = Array.from(parentElement.children).filter(
        (child) => child.tagName === current?.tagName,
      );
      if (peers.length > 1) part += `:nth-of-type(${peers.indexOf(current) + 1})`;
    }
    parts.unshift(part);
    current = parentElement;
  }
  return parts.join(" > ") || element.tagName.toLowerCase();
}

function FocusBox({ rect, number, draft = false }: {
  rect?: AnnotationRect;
  number?: number;
  draft?: boolean;
}) {
  if (!rect) return null;
  return (
    <div
      className="annotation-focus-anchor"
      style={{
        left: `${rect.x * 100}%`,
        top: `${rect.y * 100}%`,
        width: `${rect.width * 100}%`,
        height: `${rect.height * 100}%`,
      }}
    >
      <div
        className={"annotation-focus-box" + (draft ? " draft" : "")}
        style={{
          inset: SELECTION_VISUAL_INSET,
        }}
      >
        {number !== undefined && <span className="annotation-marker">{number}</span>}
      </div>
    </div>
  );
}

function SelectionOverlay({
  root,
  active,
  onRegion,
  onClick,
}: {
  root: HTMLElement | null;
  active: boolean;
  onRegion: (rect: LocalRect) => void;
  onClick?: (x: number, y: number) => void;
}) {
  const [start, setStart] = useState<{ x: number; y: number } | null>(null);
  const [current, setCurrent] = useState<{ x: number; y: number } | null>(null);

  if (!active) return null;
  const point = (event: ReactPointerEvent<HTMLDivElement>) => {
    const bounds = root?.getBoundingClientRect() || event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(bounds.width, event.clientX - bounds.left)),
      y: Math.max(0, Math.min(bounds.height, event.clientY - bounds.top)),
    };
  };
  const selection =
    start && current
      ? {
          x: Math.min(start.x, current.x),
          y: Math.min(start.y, current.y),
          width: Math.abs(current.x - start.x),
          height: Math.abs(current.y - start.y),
        }
      : null;

  return (
    <div
      className="artifact-annotation-overlay"
      data-testid="annotation-overlay"
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        const next = point(event);
        setStart(next);
        setCurrent(next);
      }}
      onPointerMove={(event) => {
        if (start) setCurrent(point(event));
      }}
      onPointerUp={(event) => {
        if (!start) return;
        const end = point(event);
        const rect = {
          x: Math.min(start.x, end.x),
          y: Math.min(start.y, end.y),
          width: Math.abs(end.x - start.x),
          height: Math.abs(end.y - start.y),
        };
        setStart(null);
        setCurrent(null);
        if (rect.width >= 6 && rect.height >= 6) onRegion(rect);
        else onClick?.(end.x, end.y);
      }}
    >
      {selection && selection.width > 1 && selection.height > 1 && (
        <div
          className="annotation-live-box"
          style={{
            left: selection.x,
            top: selection.y,
            width: selection.width,
            height: selection.height,
          }}
        />
      )}
    </div>
  );
}

function ImageSurface({
  dataUrl,
  annotating,
  focusAnnotation,
  stagedAnnotations = [],
  draftTarget,
  onSelection,
}: Omit<SurfaceProps, "kind" | "content">) {
  const root = useRef<HTMLDivElement | null>(null);
  const image = useRef<HTMLImageElement | null>(null);

  const select = async (rect: LocalRect) => {
    const img = image.current;
    const container = root.current;
    if (!img || !container) return;
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext("2d")?.drawImage(img, 0, 0);
    onSelection({
      target: {
        kind: "region",
        rect: normalizedRect(rect, container.clientWidth, container.clientHeight),
      },
      preview: cropCanvas(
        canvas,
        rect,
        container.clientWidth,
        container.clientHeight,
      ),
      anchor: clientRectFor(container, rect),
    });
  };

  return (
    <div className="annotation-image-stage">
      <div className="annotation-image-surface" ref={root}>
        <img ref={image} className="artifact-image" src={dataUrl} alt="" />
        {stagedAnnotations.map((annotation, index) => (
          <FocusBox key={annotation.id} rect={annotation.target.rect} number={index + 1} />
        ))}
        {draftTarget ? (
          <FocusBox rect={draftTarget.rect} draft />
        ) : (
          <FocusBox rect={focusAnnotation?.target.rect} />
        )}
        <SelectionOverlay
          root={root.current}
          active={annotating}
          onRegion={(rect) => void select(rect)}
        />
      </div>
    </div>
  );
}

function MarkdownSurface({
  content,
  annotating,
  focusAnnotation,
  stagedAnnotations = [],
  draftTarget,
  onSelection,
}: Omit<SurfaceProps, "kind" | "dataUrl">) {
  const root = useRef<HTMLDivElement | null>(null);

  const finish = async (rect: LocalRect, target: AnnotationTarget) => {
    const element = root.current;
    if (!element) return;
    const preview = await captureDom(element, rect);
    onSelection({
      target,
      preview,
      anchor: clientRectFor(element, rect),
    });
  };

  const selectRegion = (rect: LocalRect) => {
    const element = root.current;
    if (!element) return;
    void finish(rect, {
      kind: "region",
      rect: normalizedRect(rect, element.scrollWidth, element.scrollHeight),
    });
  };

  const selectBlock = (x: number, y: number) => {
    const element = root.current;
    if (!element) return;
    const bounds = element.getBoundingClientRect();
    const candidates = document.elementsFromPoint(bounds.left + x, bounds.top + y);
    const block = candidates
      .map((candidate) => candidate.closest(BLOCK_SELECTOR))
      .find((candidate): candidate is HTMLElement =>
        !!candidate && element.contains(candidate) && candidate !== element,
      );
    if (!block) return;
    const blockBounds = block.getBoundingClientRect();
    const rect = {
      x: blockBounds.left - bounds.left,
      y: blockBounds.top - bounds.top,
      width: blockBounds.width,
      height: blockBounds.height,
    };
    void finish(rect, {
      kind: "dom",
      selector: cssPath(block, element),
      tag: block.tagName.toLowerCase(),
      exact: (block.textContent || "").trim().slice(0, 20_000),
      rect: normalizedRect(rect, element.scrollWidth, element.scrollHeight),
    });
  };

  return (
    <div className="artifact-md annotation-document-surface" ref={root}>
      <Markdown text={content || ""} />
      {stagedAnnotations.map((annotation, index) => (
        <FocusBox key={annotation.id} rect={annotation.target.rect} number={index + 1} />
      ))}
      {draftTarget ? (
        <FocusBox rect={draftTarget.rect} draft />
      ) : (
        <FocusBox rect={focusAnnotation?.target.rect} />
      )}
      <SelectionOverlay
        root={root.current}
        active={annotating}
        onRegion={selectRegion}
        onClick={selectBlock}
      />
    </div>
  );
}

function HtmlSurface({
  content,
  annotating,
  focusAnnotation,
  stagedAnnotations = [],
  draftTarget,
  onSelection,
  reloadKey,
}: Omit<SurfaceProps, "kind" | "dataUrl">) {
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [loaded, setLoaded] = useState(0);

  useEffect(() => {
    const iframe = frame.current;
    const doc = iframe?.contentDocument;
    const win = iframe?.contentWindow;
    if (!iframe || !doc || !win || !doc.body) return;
    doc.querySelectorAll(".artifact-annotation-overlay").forEach((node) => node.remove());
    if (!annotating) return;

    const overlay = doc.createElement("div");
    overlay.className = "openworker-annotation-control artifact-annotation-overlay";
    Object.assign(overlay.style, {
      position: "fixed",
      inset: "0",
      zIndex: "2147483646",
      cursor: "crosshair",
      background: "transparent",
    });
    const live = doc.createElement("div");
    Object.assign(live.style, {
      position: "fixed",
      border: "2px dashed #2f80ed",
      background: "rgba(47,128,237,.14)",
      pointerEvents: "none",
      display: "none",
    });
    overlay.appendChild(live);
    doc.body.appendChild(overlay);
    let start: { x: number; y: number } | null = null;

    const capture = async (rect: LocalRect, target: AnnotationTarget, anchor: DOMRect) => {
      const canvas = await html2canvas(doc.documentElement, {
        backgroundColor: "#ffffff",
        foreignObjectRendering: true,
        logging: false,
        scale: 1,
        useCORS: true,
        ignoreElements: (element) =>
          element.classList.contains("openworker-annotation-control"),
      });
      onSelection({
        target,
        preview: cropCanvas(
          canvas,
          rect,
          doc.documentElement.scrollWidth,
          doc.documentElement.scrollHeight,
        ),
        anchor,
      });
    };

    const down = (event: globalThis.PointerEvent) => {
      if (event.button !== 0) return;
      start = { x: event.clientX, y: event.clientY };
      overlay.setPointerCapture(event.pointerId);
    };
    const move = (event: globalThis.PointerEvent) => {
      if (!start) return;
      const rect = {
        x: Math.min(start.x, event.clientX),
        y: Math.min(start.y, event.clientY),
        width: Math.abs(event.clientX - start.x),
        height: Math.abs(event.clientY - start.y),
      };
      Object.assign(live.style, {
        display: "block",
        left: `${rect.x}px`,
        top: `${rect.y}px`,
        width: `${rect.width}px`,
        height: `${rect.height}px`,
      });
    };
    const up = (event: globalThis.PointerEvent) => {
      if (!start) return;
      const viewportRect = {
        x: Math.min(start.x, event.clientX),
        y: Math.min(start.y, event.clientY),
        width: Math.abs(event.clientX - start.x),
        height: Math.abs(event.clientY - start.y),
      };
      start = null;
      live.style.display = "none";
      const frameBounds = iframe.getBoundingClientRect();
      if (viewportRect.width >= 6 && viewportRect.height >= 6) {
        const rect = {
          x: viewportRect.x + win.scrollX,
          y: viewportRect.y + win.scrollY,
          width: viewportRect.width,
          height: viewportRect.height,
        };
        void capture(
          rect,
          {
            kind: "region",
            rect: normalizedRect(
              rect,
              doc.documentElement.scrollWidth,
              doc.documentElement.scrollHeight,
            ),
          },
          new DOMRect(
            frameBounds.left + viewportRect.x,
            frameBounds.top + viewportRect.y,
            viewportRect.width,
            viewportRect.height,
          ),
        );
        return;
      }

      overlay.style.display = "none";
      const hit = doc.elementFromPoint(event.clientX, event.clientY);
      overlay.style.display = "block";
      const block = hit?.closest(BLOCK_SELECTOR);
      if (!block || !doc.body.contains(block)) return;
      const blockBounds = block.getBoundingClientRect();
      const rect = {
        x: blockBounds.left + win.scrollX,
        y: blockBounds.top + win.scrollY,
        width: blockBounds.width,
        height: blockBounds.height,
      };
      void capture(
        rect,
        {
          kind: "dom",
          selector: cssPath(block, doc.body),
          tag: block.tagName.toLowerCase(),
          exact: (block.textContent || "").trim().slice(0, 20_000),
          rect: normalizedRect(
            rect,
            doc.documentElement.scrollWidth,
            doc.documentElement.scrollHeight,
          ),
        },
        new DOMRect(
          frameBounds.left + blockBounds.left,
          frameBounds.top + blockBounds.top,
          blockBounds.width,
          blockBounds.height,
        ),
      );
    };
    overlay.addEventListener("pointerdown", down);
    overlay.addEventListener("pointermove", move);
    overlay.addEventListener("pointerup", up);
    return () => overlay.remove();
  }, [annotating, content, loaded, onSelection, reloadKey]);

  useEffect(() => {
    const iframe = frame.current;
    const doc = iframe?.contentDocument;
    const win = iframe?.contentWindow;
    if (!iframe || !doc?.body || !win) return;
    doc.querySelectorAll(".openworker-annotation-focus").forEach((node) => node.remove());
    const targets = stagedAnnotations.map((annotation, index) => ({
      target: annotation.target,
      number: index + 1,
      draft: false,
    }));
    if (draftTarget) targets.push({ target: draftTarget, number: 0, draft: true });
    else if (focusAnnotation) {
      targets.push({ target: focusAnnotation.target, number: 0, draft: false });
    }
    const boxes = targets.map(({ target: nextTarget, number, draft }) => {
      const anchor = doc.createElement("div");
      anchor.className = "openworker-annotation-control openworker-annotation-focus";
      const rect = nextTarget.rect;
      Object.assign(anchor.style, {
        position: "absolute",
        zIndex: "2147483645",
        pointerEvents: "none",
        left: `${rect.x * doc.documentElement.scrollWidth}px`,
        top: `${rect.y * doc.documentElement.scrollHeight}px`,
        width: `${rect.width * doc.documentElement.scrollWidth}px`,
        height: `${rect.height * doc.documentElement.scrollHeight}px`,
      });
      const box = doc.createElement("div");
      Object.assign(box.style, {
        position: "absolute",
        inset: SELECTION_VISUAL_INSET,
        border: `${draft ? "1px dashed" : "1.5px solid"} #2f80ed`,
        background: "rgba(47,128,237,.10)",
        boxSizing: "border-box",
      });
      if (number > 0) {
        const marker = doc.createElement("span");
        marker.textContent = String(number);
        Object.assign(marker.style, {
          position: "absolute",
          top: "-11px",
          right: "-11px",
          width: "20px",
          height: "20px",
          display: "grid",
          placeItems: "center",
          border: "2px solid white",
          borderRadius: "50%",
          background: "#2f80ed",
          color: "white",
          font: "600 11px system-ui, sans-serif",
          boxSizing: "border-box",
        });
        box.appendChild(marker);
      }
      anchor.appendChild(box);
      doc.body.appendChild(anchor);
      return anchor;
    });
    boxes[boxes.length - 1]?.scrollIntoView({ block: "center", inline: "center" });
    return () => boxes.forEach((box) => box.remove());
  }, [draftTarget, focusAnnotation, loaded, stagedAnnotations]);

  return (
    <iframe
      key={reloadKey}
      ref={frame}
      sandbox="allow-scripts allow-same-origin"
      className="artifact-frame"
      srcDoc={content || ""}
      onLoad={() => setLoaded((value) => value + 1)}
    />
  );
}

interface PdfLine {
  text: string;
  rect: LocalRect;
}

function PdfPage({
  page,
  pageNumber,
  width,
  pdfjs,
  annotating,
  focusAnnotation,
  stagedAnnotations = [],
  draftTarget,
  onSelection,
}: {
  page: any;
  pageNumber: number;
  width: number;
  pdfjs: any;
  annotating: boolean;
  focusAnnotation?: ArtifactAnnotation | null;
  stagedAnnotations?: ArtifactAnnotation[];
  draftTarget?: AnnotationTarget;
  onSelection: (selection: CapturedSelection) => void;
}) {
  const root = useRef<HTMLDivElement | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [height, setHeight] = useState(0);
  const [lines, setLines] = useState<PdfLine[]>([]);

  useLayoutEffect(() => {
    let cancelled = false;
    let renderTask: { promise: Promise<unknown>; cancel: () => void } | null = null;
    const render = async () => {
      try {
        const base = page.getViewport({ scale: 1 });
        const scale = width / base.width;
        const viewport = page.getViewport({ scale });
        const dpr = window.devicePixelRatio || 1;
        const target = canvas.current;
        if (!target) return;
        setHeight(viewport.height);
        target.style.width = `${viewport.width}px`;
        target.style.height = `${viewport.height}px`;
        target.width = Math.ceil(viewport.width * dpr);
        target.height = Math.ceil(viewport.height * dpr);
        const task = page.render({
          canvasContext: target.getContext("2d")!,
          viewport: page.getViewport({ scale: scale * dpr }),
        });
        renderTask = task;
        await task.promise;
        const text = await page.getTextContent();
        if (cancelled) return;
        const items = text.items
          .filter((item: any) => typeof item.str === "string" && item.str.trim())
          .map((item: any) => {
            const transform = pdfjs.Util.transform(viewport.transform, item.transform);
            const fontHeight = Math.max(8, Math.hypot(transform[2], transform[3]));
            return {
              text: item.str,
              rect: {
                x: transform[4],
                y: transform[5] - fontHeight,
                width: Math.max(2, item.width * scale),
                height: fontHeight,
              },
            };
          })
          .sort((a: PdfLine, b: PdfLine) =>
            Math.abs(a.rect.y - b.rect.y) < 3
              ? a.rect.x - b.rect.x
              : a.rect.y - b.rect.y,
          );
        const grouped: PdfLine[] = [];
        for (const item of items) {
          const line = grouped.find(
            (candidate) =>
              Math.abs(candidate.rect.y - item.rect.y)
              < Math.max(4, item.rect.height * 0.45),
          );
          if (!line) {
            grouped.push({ text: item.text, rect: { ...item.rect } });
            continue;
          }
          const right = Math.max(line.rect.x + line.rect.width, item.rect.x + item.rect.width);
          const bottom = Math.max(line.rect.y + line.rect.height, item.rect.y + item.rect.height);
          line.rect.x = Math.min(line.rect.x, item.rect.x);
          line.rect.y = Math.min(line.rect.y, item.rect.y);
          line.rect.width = right - line.rect.x;
          line.rect.height = bottom - line.rect.y;
          line.text += `${line.text.endsWith(" ") ? "" : " "}${item.text}`;
        }
        setLines(grouped);
      } catch (reason: any) {
        if (!cancelled && reason?.name !== "RenderingCancelledException") throw reason;
      }
    };
    void render();
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [page, pdfjs, width]);

  const finish = (rect: LocalRect, target: AnnotationTarget) => {
    const pageCanvas = canvas.current;
    const element = root.current;
    if (!pageCanvas || !element || !height) return;
    onSelection({
      target,
      preview: cropCanvas(pageCanvas, rect, width, height),
      anchor: clientRectFor(element, rect),
    });
  };

  const selectRegion = (rect: LocalRect) => {
    finish(rect, {
      kind: "region",
      page: pageNumber,
      rect: normalizedRect(rect, width, height),
    });
  };
  const selectLine = (x: number, y: number) => {
    const line = lines.find(
      (candidate) =>
        x >= candidate.rect.x - 4
        && x <= candidate.rect.x + candidate.rect.width + 4
        && y >= candidate.rect.y - 3
        && y <= candidate.rect.y + candidate.rect.height + 3,
    );
    if (!line) return;
    finish(line.rect, {
      kind: "text",
      page: pageNumber,
      exact: line.text.trim(),
      rect: normalizedRect(line.rect, width, height),
    });
  };
  const activeTarget = draftTarget || focusAnnotation?.target;
  const focused =
    activeTarget
    && ("page" in activeTarget ? activeTarget.page === pageNumber : pageNumber === 1)
      ? activeTarget.rect
      : undefined;

  return (
    <div
      className="artifact-pdf-page-wrap"
      ref={root}
      style={{ width, height: height || undefined }}
    >
      <canvas ref={canvas} className="artifact-pdf-page" />
      <div className="artifact-pdf-text-layer" aria-hidden="true">
        {lines.map((line, index) => (
          <span
            key={index}
            style={{
              left: line.rect.x,
              top: line.rect.y,
              width: line.rect.width,
              height: line.rect.height,
            }}
          />
        ))}
      </div>
      {stagedAnnotations.map((annotation, index) => {
        const target = annotation.target;
        const visible =
          ("page" in target ? target.page === pageNumber : pageNumber === 1);
        return visible ? (
          <FocusBox key={annotation.id} rect={target.rect} number={index + 1} />
        ) : null;
      })}
      {draftTarget ? (
        <FocusBox rect={focused} draft />
      ) : (
        <FocusBox rect={focused} />
      )}
      <SelectionOverlay
        root={root.current}
        active={annotating}
        onRegion={selectRegion}
        onClick={selectLine}
      />
    </div>
  );
}

function PdfSurface({
  dataUrl,
  annotating,
  focusAnnotation,
  stagedAnnotations = [],
  draftTarget,
  onSelection,
}: Omit<SurfaceProps, "kind" | "content">) {
  const holder = useRef<HTMLDivElement | null>(null);
  const [pdfjs, setPdfjs] = useState<any>(null);
  const [pages, setPages] = useState<any[]>([]);
  const [width, setWidth] = useState(640);
  const [error, setError] = useState("");

  useEffect(() => {
    const element = holder.current;
    if (!element) return;
    const resize = () => setWidth(Math.max(240, element.clientWidth - 32));
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setPages([]);
    setError("");
    const base64 = (dataUrl || "").split(",")[1] || "";
    import("pdfjs-dist")
      .then(async (module) => {
        module.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
        const document = await module.getDocument({ data: bytes }).promise;
        const loaded = [];
        for (let index = 1; index <= document.numPages; index++) {
          loaded.push(await document.getPage(index));
        }
        if (!cancelled) {
          setPdfjs(module);
          setPages(loaded);
        }
      })
      .catch((reason) => !cancelled && setError(String(reason?.message || reason)));
    return () => {
      cancelled = true;
    };
  }, [dataUrl]);

  if (error) {
    return <div className="rail-error artifact-table-note">Could not render PDF: {error}</div>;
  }
  return (
    <div className="artifact-pdfjs" ref={holder}>
      {!pages.length && <div className="rail-muted artifact-table-note">Rendering PDF…</div>}
      {pages.map((page, index) => (
        <PdfPage
          key={index}
          page={page}
          pageNumber={index + 1}
          width={width}
          pdfjs={pdfjs}
          annotating={annotating}
          focusAnnotation={focusAnnotation}
          stagedAnnotations={stagedAnnotations}
          draftTarget={draftTarget}
          onSelection={onSelection}
        />
      ))}
    </div>
  );
}

export function ArtifactAnnotationSurface(props: SurfaceProps) {
  switch (props.kind) {
    case "image":
      return <ImageSurface {...props} />;
    case "markdown":
      return <MarkdownSurface {...props} />;
    case "html":
      return <HtmlSurface {...props} />;
    case "pdf":
      return <PdfSurface {...props} />;
  }
}
