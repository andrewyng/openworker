import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import { useTranslation } from "react-i18next";
import remarkGfm from "remark-gfm";
import { Icon } from "./Icon";

// §34 (UX-016): the agent ends a deliverable turn with plain markdown —
// [Title](artifact:relative/path) — and the renderer turns it into a chip that opens the
// artifact viewer in place. Plumbing is a window event (the viewer lives in RightRail;
// this component renders deep inside the transcript): RightRail resolves the path against
// the session's artifact list, App un-hides the rail.
export const OPEN_ARTIFACT_EVENT = "ocw-open-artifact";

/** Normalize an artifact: href path for the session workspace.
 *
 * react-markdown / micromark percent-encodes non-ASCII URL characters, so
 * `artifact:reports/报告.md` becomes `artifact:reports/%E6%8A%A5%E5%91%8A.md`.
 * The backend looks up the literal filesystem path — encoded names 404 as "not found".
 * Also strip a single leading `/` so `artifact:/reports/x.md` stays workspace-relative
 * (Path(workspace) / "/abs" would otherwise escape the workspace root).
 */
export function normalizeArtifactPath(raw: string): string {
  let path = raw;
  try {
    path = decodeURIComponent(raw);
  } catch {
    // malformed % sequences — keep raw
  }
  if (path.startsWith("/")) path = path.replace(/^\/+/, "");
  return path;
}

function ArtifactChip({ path, title }: { path: string; title: string }) {
  const { t } = useTranslation();
  const resolved = normalizeArtifactPath(path);
  const file = resolved.split("/").pop() || resolved;
  return (
    <button
      className="art-chip"
      data-testid="artifact-chip"
      title={resolved}
      onClick={() =>
        window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: resolved } }))
      }
    >
      <span className="art-chip-ico">
        <Icon name="file" size={14} />
      </span>
      <span className="art-chip-meta">
        <b>{title || file}</b>
        {title && title !== file && <span>{file}</span>}
      </span>
      <span className="art-chip-open">{t("rail.open")} ›</span>
    </button>
  );
}

// Assistant messages rendered as GitHub-flavored markdown (headings, lists, tables, code,
// links). Links open externally — never navigate the app shell — except artifact: links,
// which open the session's artifact viewer.
export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // artifact: is ours — keep it through the sanitizer (everything else gets the default
        // http/https/mailto policy).
        urlTransform={(url) => (url.startsWith("artifact:") ? url : defaultUrlTransform(url))}
        components={{
          a: ({ node: _n, href, children, ...props }) => {
            if (href?.startsWith("artifact:")) {
              const title = Array.isArray(children) ? children.join("") : String(children ?? "");
              return <ArtifactChip path={href.slice("artifact:".length)} title={title} />;
            }
            return (
              <a href={href} {...props} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
