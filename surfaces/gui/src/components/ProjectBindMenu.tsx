// UX-044: the "This session" submenus of the composer "+" menu — bind a named
// project memory or board, name the current one, or (memory) open the editor.
// Radio semantics: picking a row IS the swap. The derived row is pinned first,
// labeled per the UX-044 rules (git = repo name with a branch glyph; folder =
// ~-collapsed path trimmed to its last 3 segments). Named rows are MRU-ordered,
// 5 visible; at 6+ a filter appears and the list is the results (no second
// surface). Project memory only — global memory never appears here.

import { useEffect, useRef, useState } from "react";
import { getProjectMenu, nameCurrentProject, setProjectBinding } from "../api";
import type { ProjectMenu } from "../api";
import { Icon } from "./Icon";

const MRU_VISIBLE = 5;
const FILTER_AT = 6;

// "~/fleet/ro4d/demo-universe/notes" → "…/ro4d/demo-universe/notes"
export function trimPath(label: string): string {
  const parts = label.split("/").filter(Boolean);
  if (parts.length <= 3) return label;
  return "…/" + parts.slice(-3).join("/");
}

export function ProjectBindMenu(props: {
  sessionId: string;
  kind: "memory" | "board";
  onClose: () => void;
  onOpenMemory?: () => void;
}) {
  const { sessionId, kind } = props;
  const [menu, setMenu] = useState<ProjectMenu | null>(null);
  const [filter, setFilter] = useState("");
  const [naming, setNaming] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nameInput = useRef<HTMLInputElement>(null);

  const reload = () => {
    getProjectMenu(sessionId, kind).then(setMenu).catch(() => setMenu(null));
  };
  useEffect(reload, [sessionId, kind]);
  useEffect(() => {
    if (naming) nameInput.current?.focus();
  }, [naming]);

  const bind = async (name: string | null) => {
    const res = await setProjectBinding(sessionId, kind, name);
    if (!res.ok) {
      setError(res.error || "could not bind");
      return;
    }
    props.onClose();
  };

  const nameCurrent = async () => {
    const name = nameDraft.trim();
    if (!name) return;
    const res = await nameCurrentProject(sessionId, kind, name);
    if (!res.ok) {
      setError(res.error || "could not name");
      return;
    }
    setNaming(false);
    setNameDraft("");
    reload();
  };

  const named = menu?.named ?? [];
  const q = filter.trim().toLowerCase();
  const shown = q
    ? named.filter((n) => n.name.toLowerCase().includes(q))
    : named.slice(0, MRU_VISIBLE);
  const title = kind === "memory" ? "Project memory" : "Board";

  const radioRow = (
    active: boolean,
    label: React.ReactNode,
    onClick: () => void,
    key: string,
    tag?: string,
  ) => (
    <button
      key={key}
      className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left hover:bg-paper"
      onClick={onClick}
    >
      <span
        className={
          "inline-block w-3.5 h-3.5 rounded-full border shrink-0 " +
          (active ? "border-accent border-[4.5px]" : "border-faint")
        }
      />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {tag && <span className="text-[10.5px] text-faint shrink-0">{tag}</span>}
    </button>
  );

  return (
    <div
      className="absolute z-50 bottom-full mb-1 left-[190px] w-[248px] rounded-xl border border-line bg-panel shadow-2xl py-1.5"
      data-testid={`project-menu-${kind}`}
    >
      <div className="px-3 pt-1 pb-1.5 text-[10.5px] font-semibold tracking-wide uppercase text-faint">
        {title}
      </div>
      {named.length >= FILTER_AT && (
        <div className="mx-2.5 mb-1.5 flex items-center gap-1.5 rounded-lg border border-line px-2 py-1">
          <Icon name="search" size={12} className="text-faint shrink-0" />
          <input
            className="w-full bg-transparent text-[12.5px] outline-none"
            placeholder="Filter…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      )}
      {kind === "board" &&
        !q &&
        radioRow(!menu?.bound && !menu?.derived, "none", () => bind(null), "none")}
      {menu?.derived &&
        !q &&
        radioRow(
          !menu.bound,
          menu.derived.kind === "git" ? (
            <span className="inline-flex items-center gap-1.5">
              <Icon name="branch" size={12} className="text-faint shrink-0" />
              <span className="font-mono text-[12px]">{menu.derived.label}</span>
            </span>
          ) : (
            <span className="font-mono text-[12px] text-muted" title={menu.derived.full}>
              {trimPath(menu.derived.label)}
            </span>
          ),
          () => bind(null),
          "derived",
          menu.derived.kind === "git" ? "this repo" : "this folder",
        )}
      {shown.map((n) =>
        radioRow(
          menu?.bound === n.name,
          n.name,
          () => bind(n.name),
          n.name,
          menu?.bound === n.name ? "bound" : undefined,
        ),
      )}
      {q && shown.length === 0 && (
        <div className="px-3 py-1.5 text-[12px] text-faint">no matches</div>
      )}
      <div className="my-1 border-t border-line" />
      {naming ? (
        <div className="mx-2.5 my-1 flex items-center gap-1.5">
          <input
            ref={nameInput}
            className="w-full rounded-lg border border-line bg-transparent px-2 py-1 text-[12.5px] outline-none"
            placeholder={`Name this ${kind}…`}
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") nameCurrent();
              if (e.key === "Escape") setNaming(false);
            }}
          />
        </div>
      ) : (
        <button
          className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[12.5px] text-left text-muted hover:bg-paper"
          onClick={() => setNaming(true)}
        >
          <Icon name="pencil" size={13} className="shrink-0" /> Name current {kind}…
        </button>
      )}
      {kind === "memory" && props.onOpenMemory && (
        <button
          className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[12.5px] text-left text-muted hover:bg-paper"
          onClick={() => {
            props.onClose();
            props.onOpenMemory?.();
          }}
        >
          <Icon name="sliders" size={13} className="shrink-0" /> View &amp; edit…
        </button>
      )}
      {error && <div className="px-3 py-1 text-[11.5px] text-red-500">{error}</div>}
    </div>
  );
}
