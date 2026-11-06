import { useState } from "react";
import { useTranslation } from "react-i18next";
import { parse } from "yaml";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";

// Same fence rule as coworker/personas/manifest.py: a file opens with --- and the closer
// is the next line that is exactly ---. A later --- in the body (a horizontal rule, a
// code fence) stays in the body. Invalid YAML, or a value that is not a mapping, leaves
// the file whole so a bad block cannot blank the preview.
export function splitMarkdownFrontmatter(text: string): {
  meta: Record<string, unknown> | null;
  body: string;
} {
  const normalized = text.replace(/^\uFEFF/, "");
  const open = normalized.match(/^---[ \t]*\r?\n/);
  if (!open) return { meta: null, body: text };
  // Start on the newline that ends the opening fence, so an empty block (---\n---)
  // still finds its closer. manifest.py searches from that same newline.
  const closer = findCloser(normalized, open[0].length - 1);
  if (!closer) return { meta: null, body: text };
  const raw = normalized.slice(open[0].length, closer.index);
  const body = normalized.slice(closer.index + closer.length).replace(/^[\r\n]+/, "");
  try {
    const meta = asMapping(parse(raw));
    if (!meta) return { meta: null, body: text };
    return { meta, body };
  } catch {
    return { meta: null, body: text };
  }
}

function findCloser(text: string, from: number): { index: number; length: number } | null {
  let i = from;
  while (i < text.length) {
    const at = text.indexOf("\n---", i);
    if (at === -1) return null;
    const match = text.slice(at).match(/^\n---[ \t]*(?:\r?\n|$)/);
    if (match) return { index: at, length: match[0].length };
    i = at + 4;
  }
  return null;
}

function asMapping(value: unknown): Record<string, unknown> | null {
  if (value === null || value === undefined) return {};
  if (typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function isScalar(value: unknown): boolean {
  return value === null || value === undefined || ["string", "number", "boolean"].includes(typeof value);
}

function isScalarArray(value: unknown): value is unknown[] {
  return Array.isArray(value) && value.every(isScalar);
}

function isNested(value: unknown): boolean {
  return !isScalar(value) && !isScalarArray(value);
}

function oneLine(value: unknown): string {
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return String(value);
  }
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "";
  } catch {
    return String(value);
  }
}

function ScalarValue({ name, value }: { name: string; value: unknown }) {
  if (typeof value === "boolean") {
    return (
      <input
        type="checkbox"
        className="md-prop-check"
        checked={value}
        disabled
        aria-label={name}
        readOnly
      />
    );
  }
  if (isScalarArray(value)) {
    return (
      <span className="md-prop-chips">
        {value.map((item, i) => (
          <span className="md-prop-chip" key={`${String(item)}-${i}`}>
            {item === null || item === undefined ? "null" : String(item)}
          </span>
        ))}
      </span>
    );
  }
  if (value === null || value === undefined) return <span className="md-prop-null">null</span>;
  return <>{String(value)}</>;
}

function PropertyRow({ name, value }: { name: string; value: unknown }) {
  const nested = isNested(value);
  const [open, setOpen] = useState(false);
  return (
    <div className="md-prop-row">
      <div className="md-prop-key">
        {nested ? (
          <button
            type="button"
            className="md-prop-twist"
            aria-expanded={open}
            aria-label={name}
            onClick={() => setOpen((v) => !v)}
          >
            <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
          </button>
        ) : (
          <span className="md-prop-twist-spacer" />
        )}
        <span>{name}</span>
      </div>
      <div className="md-prop-value">
        {nested ? (
          <div>
            <button type="button" className="md-prop-nested" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
              {open ? <pre>{pretty(value)}</pre> : <span className="md-prop-nested-line">{oneLine(value)}</span>}
            </button>
          </div>
        ) : (
          <ScalarValue name={name} value={value} />
        )}
      </div>
    </div>
  );
}

function MarkdownProperties({ meta }: { meta: Record<string, unknown> }) {
  const { t } = useTranslation();
  const entries = Object.entries(meta);
  if (!entries.length) return null;
  return (
    <div className="md-props" data-testid="markdown-properties">
      <div className="md-props-title">{t("rail.properties")}</div>
      {entries.map(([key, value]) => (
        <PropertyRow key={key} name={key} value={value} />
      ))}
    </div>
  );
}

// Preview renders YAML frontmatter as a read-only properties block (Cursor's layout,
// without its editor) and the markdown body underneath. Source is the raw file.
export function MarkdownFileView({ text, mode }: { text: string; mode: "preview" | "source" }) {
  if (mode === "source") {
    return (
      <pre className="artifact-code" data-testid="markdown-source">
        {text}
      </pre>
    );
  }
  const { meta, body } = splitMarkdownFrontmatter(text);
  return (
    <div className="artifact-md" data-testid="markdown-preview">
      {meta && <MarkdownProperties meta={meta} />}
      <Markdown text={body} />
    </div>
  );
}
