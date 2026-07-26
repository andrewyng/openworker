import { useEffect, useRef, useState } from "react";
import {
  createSkill,
  deleteSkill,
  draftSkill,
  getRecentWorkspaces,
  listSkills,
  moveSkill,
  stageSkillUpload,
  confirmSkillUpload,
  updateSkill,
  type RecentWorkspace,
  type SkillRow,
  type SkillUploadPreview,
} from "../api";
import { Icon } from "./Icon";

// Settings ▸ Skills (SKILLS-SPEC §4.1/§4.2) — the management home: list + permanent
// enable/disable + the three add modes (write / upload / draft-with-OpenWorker).
// Scope = folder location; the picker defaults "Everywhere" and only offers "Only in a
// project" when a workspace is known (two-doors: the rail's manage-link passes the
// session's workspace so the project option arrives preselected).

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-[12.5px] font-medium text-ink";
const INPUT =
  "w-full min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const BTN_ACCENT =
  "text-[12.5px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-[12.5px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";
const BADGE =
  "text-[11px] px-2 py-0.5 rounded-full border border-line bg-paper text-muted shrink-0";

type Editor = {
  mode: "new" | "edit";
  name: string;
  description: string;
  instructions: string;
  scope: "global" | "project";
  workspace: string;
};

const emptyEditor = (scope: "global" | "project", workspace: string): Editor => ({
  mode: "new",
  name: "",
  description: "",
  instructions: "",
  scope,
  workspace,
});

async function fileToB64(file: File): Promise<string> {
  // FileReader fallback: File.arrayBuffer is missing in some webviews (and jsdom).
  const buf =
    typeof file.arrayBuffer === "function"
      ? await file.arrayBuffer()
      : await new Promise<ArrayBuffer>((resolve, reject) => {
          const r = new FileReader();
          r.onload = () => resolve(r.result as ArrayBuffer);
          r.onerror = () => reject(r.error);
          r.readAsArrayBuffer(file);
        });
  const bytes = new Uint8Array(buf);
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

export function SkillsTab({ workspaceContext }: { workspaceContext?: string }) {
  const [rows, setRows] = useState<SkillRow[]>([]);
  const [workspaces, setWorkspaces] = useState<RecentWorkspace[]>([]);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [upload, setUpload] = useState<SkillUploadPreview | null>(null);
  const [uploadScope, setUploadScope] = useState<"global" | "project">("global");
  const [draftText, setDraftText] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [armedDelete, setArmedDelete] = useState<string | null>(null);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = () => listSkills(workspaceContext).then(setRows);
  useEffect(() => {
    refresh();
    getRecentWorkspaces().then((ws) => setWorkspaces(ws.filter((w) => w.exists)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceContext]);

  // Two-doors (§4.3): arriving from a session preselects "Only in <that project>".
  const defaultScope: "global" | "project" = workspaceContext ? "project" : "global";
  const knownWorkspaces = workspaceContext
    ? [{ path: workspaceContext, name: workspaceContext.split(/[\\/]/).pop() || workspaceContext, exists: true }]
    : workspaces;
  const projectOptionAvailable = knownWorkspaces.length > 0;

  const fail = (res: { ok?: boolean; error?: string }) => {
    if (res.ok === false) {
      setError(res.error || "Something went wrong.");
      return true;
    }
    setError("");
    return false;
  };

  const save = async () => {
    if (!editor) return;
    const workspace = editor.scope === "project" ? editor.workspace : undefined;
    const res =
      editor.mode === "new"
        ? await createSkill({
            name: editor.name.trim(),
            description: editor.description.trim(),
            instructions: editor.instructions,
            scope: editor.scope,
            workspace,
          })
        : await updateSkill(editor.name, {
            description: editor.description.trim(),
            instructions: editor.instructions,
            workspace: workspaceContext,
          });
    if (fail(res)) return;
    setEditor(null);
    refresh();
  };

  const draft = async () => {
    setDrafting(true);
    const res = await draftSkill(draftText);
    setDrafting(false);
    if (fail(res)) return;
    // The draft only fills the form — the user reviews and saves (never auto-saved, §4.2).
    setEditor({
      ...emptyEditor(defaultScope, workspaceContext || knownWorkspaces[0]?.path || ""),
      name: res.name || "",
      description: res.description || "",
      instructions: res.instructions || "",
    });
    setDraftText("");
  };

  const onPickFile = async (file: File | undefined) => {
    if (!file) return;
    const res = await stageSkillUpload(await fileToB64(file), file.name);
    if (fail(res)) return;
    setUploadScope(defaultScope);
    setUpload(res);
  };

  const confirmUpload = async () => {
    if (!upload?.token) return;
    const workspace =
      uploadScope === "project"
        ? workspaceContext || knownWorkspaces[0]?.path
        : undefined;
    const res = await confirmSkillUpload(upload.token, uploadScope, workspace);
    if (fail(res)) return;
    setUpload(null);
    refresh();
  };

  const remove = async (row: SkillRow) => {
    if (armedDelete !== row.name) {
      setArmedDelete(row.name);
      return;
    }
    setArmedDelete(null);
    const res = await deleteSkill(row.name, row.scope === "project" ? workspaceContext : undefined);
    if (fail(res)) return;
    refresh();
  };

  const move = async (row: SkillRow) => {
    const to = row.scope === "global" ? "project" : "global";
    const workspace = workspaceContext || knownWorkspaces[0]?.path;
    const res = await moveSkill(row.name, to, workspace);
    if (fail(res)) return;
    refresh();
  };

  return (
    <section>
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h2 className="text-[16px] font-semibold">Skills</h2>
          <p className="text-[12.5px] text-muted mt-1 leading-relaxed">
            Reusable instructions the worker can follow — available everywhere or only in one
            project. Off here means off in every session.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            className={BTN_BORDERED}
            onClick={() => fileInput.current?.click()}
          >
            <span className="inline-flex items-center gap-1.5">
              <Icon name="plus" size={13} /> Import
            </span>
          </button>
          <button
            className={BTN_ACCENT}
            onClick={() =>
              setEditor(emptyEditor(defaultScope, workspaceContext || knownWorkspaces[0]?.path || ""))
            }
          >
            New skill
          </button>
        </div>
      </div>
      <input
        ref={fileInput}
        type="file"
        accept=".zip,.skill,.md"
        className="hidden"
        aria-label="Upload a skill archive"
        onChange={(e) => {
          onPickFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />

      {error ? (
        <div className="text-[12.5px] text-red-500 mb-3" role="alert">
          {error}
        </div>
      ) : null}

      {upload ? (
        <div className={`${CARD} p-4 mb-4`}>
          <div className="text-[13px] font-medium mb-1">Review before installing</div>
          <p className="text-[12.5px] text-muted mb-3">
            Read the instructions — installing a skill means the worker will follow them.
          </p>
          <div className="text-[13px] mb-1">
            <span className="font-medium">{upload.name}</span>
            <span className="text-muted"> — {upload.description || "no description"}</span>
          </div>
          <pre className="text-[12px] bg-paper border border-line rounded-lg p-3 whitespace-pre-wrap max-h-64 overflow-y-auto mb-2">
            {upload.instructions}
          </pre>
          {upload.files?.length ? (
            <div className="text-[12px] text-muted mb-2">
              Bundled files: {upload.files.join(", ")}
            </div>
          ) : null}
          <ScopePicker
            scope={uploadScope}
            onScope={setUploadScope}
            workspaces={knownWorkspaces}
            projectAvailable={projectOptionAvailable}
          />
          <div className="flex gap-2 mt-3">
            <button className={BTN_ACCENT} onClick={confirmUpload}>
              Install skill
            </button>
            <button className={BTN_BORDERED} onClick={() => setUpload(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {editor ? (
        <div className={`${CARD} p-4 mb-4`}>
          <div className="text-[13px] font-medium mb-3">
            {editor.mode === "new" ? "New skill" : `Edit ${editor.name}`}
          </div>
          <label className={FIELD_LABEL} htmlFor="skill-name">
            Name
          </label>
          <input
            id="skill-name"
            className={`${INPUT} mt-1 mb-3`}
            value={editor.name}
            disabled={editor.mode === "edit"}
            placeholder="weekly-report"
            onChange={(e) => setEditor({ ...editor, name: e.target.value })}
          />
          <label className={FIELD_LABEL} htmlFor="skill-desc">
            Description
          </label>
          <input
            id="skill-desc"
            className={`${INPUT} mt-1 mb-3`}
            value={editor.description}
            placeholder="One line the worker uses to decide when this applies"
            onChange={(e) => setEditor({ ...editor, description: e.target.value })}
          />
          <label className={FIELD_LABEL} htmlFor="skill-instructions">
            Instructions
          </label>
          <textarea
            id="skill-instructions"
            className={`${INPUT} mt-1 mb-3 min-h-[140px] font-mono`}
            value={editor.instructions}
            placeholder={"1. Gather last week's updates\n2. Write the report, under 300 words"}
            onChange={(e) => setEditor({ ...editor, instructions: e.target.value })}
          />
          {editor.mode === "new" ? (
            <ScopePicker
              scope={editor.scope}
              onScope={(scope) => setEditor({ ...editor, scope })}
              workspace={editor.workspace}
              onWorkspace={(workspace) => setEditor({ ...editor, workspace })}
              workspaces={knownWorkspaces}
              projectAvailable={projectOptionAvailable}
            />
          ) : null}
          <div className="flex gap-2 mt-3">
            <button
              className={BTN_ACCENT}
              disabled={!editor.name.trim() || !editor.instructions.trim()}
              onClick={save}
            >
              Save skill
            </button>
            <button className={BTN_BORDERED} onClick={() => setEditor(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <div className={`${CARD} divide-y divide-line`}>
        {rows.length === 0 && !editor ? (
          <div className="p-5 text-[13px] text-muted">
            No skills yet. Write one, import a .zip / .skill / SKILL.md someone shared, or
            describe one below and let OpenWorker draft it.
          </div>
        ) : null}
        {rows.map((row) => (
          <div key={`${row.scope}:${row.name}`} className="flex items-center gap-3 px-4 py-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className={`text-[13px] font-medium ${row.enabled ? "" : "text-muted"}`}>
                  {row.name}
                </span>
                <span className={BADGE}>{row.scope}</span>
                {row.source !== "local" ? <span className={BADGE}>{row.source}</span> : null}
              </div>
              <div className="text-[12px] text-muted truncate">{row.description}</div>
            </div>
            <button
              className={BTN_BORDERED}
              title="Edit"
              onClick={() =>
                setEditor({
                  mode: "edit",
                  name: row.name,
                  description: row.description,
                  instructions: row.instructions,
                  scope: row.scope,
                  workspace: workspaceContext || "",
                })
              }
            >
              <Icon name="pencil" size={13} />
            </button>
            {projectOptionAvailable || row.scope === "project" ? (
              <button className={BTN_BORDERED} onClick={() => move(row)}>
                {row.scope === "global" ? "Move to project" : "Move to global"}
              </button>
            ) : null}
            <button
              className={BTN_BORDERED}
              aria-label={`Delete ${row.name}`}
              onClick={() => remove(row)}
              onBlur={() => setArmedDelete(null)}
            >
              {armedDelete === row.name ? "Confirm delete" : <Icon name="trash" size={13} />}
            </button>
            <label className="inline-flex items-center gap-1.5 text-[12px] text-muted">
              <input
                type="checkbox"
                role="switch"
                aria-label={`${row.name} enabled`}
                checked={row.enabled}
                onChange={(e) => updateSkill(row.name, { enabled: e.target.checked }).then(refresh)}
              />
              On
            </label>
          </div>
        ))}
      </div>

      <div className={`${CARD} p-4 mt-4`}>
        <div className="text-[13px] font-medium mb-1">Create using OpenWorker</div>
        <p className="text-[12.5px] text-muted mb-2">
          Describe what the skill should do — a draft lands in the editor for you to review. Nothing
          is saved until you save it.
        </p>
        <textarea
          className={`${INPUT} min-h-[64px]`}
          value={draftText}
          placeholder="Every Monday I write a status report from Slack and GitHub activity…"
          aria-label="Describe the skill"
          onChange={(e) => setDraftText(e.target.value)}
        />
        <button
          className={`${BTN_ACCENT} mt-2`}
          disabled={!draftText.trim() || drafting}
          onClick={draft}
        >
          {drafting ? "Drafting…" : "Draft with OpenWorker"}
        </button>
      </div>
    </section>
  );
}

function ScopePicker({
  scope,
  onScope,
  workspace,
  onWorkspace,
  workspaces,
  projectAvailable,
}: {
  scope: "global" | "project";
  onScope: (s: "global" | "project") => void;
  workspace?: string;
  onWorkspace?: (path: string) => void;
  workspaces: RecentWorkspace[];
  projectAvailable: boolean;
}) {
  return (
    <div>
      <div className={FIELD_LABEL}>Available in</div>
      <div className="flex items-center gap-4 mt-1.5">
        <label className="inline-flex items-center gap-1.5 text-[13px]">
          <input
            type="radio"
            name="skill-scope"
            checked={scope === "global"}
            onChange={() => onScope("global")}
          />
          Everywhere
        </label>
        {projectAvailable ? (
          <label className="inline-flex items-center gap-1.5 text-[13px]">
            <input
              type="radio"
              name="skill-scope"
              checked={scope === "project"}
              onChange={() => onScope("project")}
            />
            Only one project
          </label>
        ) : null}
      </div>
      {scope === "project" && projectAvailable ? (
        <select
          className={`${INPUT} mt-2`}
          aria-label="Project"
          value={workspace || workspaces[0]?.path || ""}
          onChange={(e) => onWorkspace?.(e.target.value)}
        >
          {workspaces.map((w) => (
            <option key={w.path} value={w.path}>
              {w.name}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}
