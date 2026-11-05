// Observability Phase 3, OB-15: "How it's been doing" on the coworker page (technical-design.md
// §9, UX-059 ob15). One row per coworker version (fingerprint) over the last 30 days, on the
// machine it lives on. A run is the turns of one session under one version (TD3-12); under 10
// runs a row says "too few runs to compare" instead of medians (TD3-14). "What changed" comes
// from the kept versions' file lists (E25c); "Show diff" from their kept files (TD3-13).

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getPersonaDoing, getPersonaVersionDiff, type DoingRow, type PersonaDoing as Doing, type VersionFileChange } from "../api";
import { formatTokens } from "../usage";
import { shortDate } from "../obs3";

type T = (k: string, o?: any) => string;

export function PersonaDoing({ personaId, machineId, secClass, scrollIntoView }: {
  personaId: string; machineId?: string | null; secClass?: string; scrollIntoView?: boolean;
}) {
  const { t } = useTranslation();
  const [doing, setDoing] = useState<Doing | null>(null);
  const [diff, setDiff] = useState<{ fp: string; files: (VersionFileChange & { diff: string })[] } | null>(null);
  useEffect(() => {
    let live = true;
    setDoing(null);
    getPersonaDoing(personaId, machineId)
      .then((d) => live && setDoing(d))
      .catch(() => live && setDoing(null));
    return () => {
      live = false;
    };
  }, [personaId, machineId]);
  if (!doing || !Array.isArray(doing.rows)) return null;  // an older engine answers something else
  const reveal = (el: HTMLElement | null) => {
    if (el && scrollIntoView && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "start" });
  };
  const newest = doing.rows[0];
  return (
    <section data-testid="persona-doing" ref={reveal}>
      <div className={`${secClass || ""} mb-1.5`}>{t("p3.doing.title")}</div>
      <div className="rounded-xl2 border border-line bg-panel p-3">
        {doing.rows.length === 0 ? (
          <div className="text-meta text-faint">{t("p3.doing.none")}</div>
        ) : (
          <table className="p3-t">
            <thead>
              <tr>
                <th>{t("p3.doing.version")}</th>
                <th>{t("p3.doing.in_use")}</th>
                <th className="n">{t("p3.doing.runs")}</th>
                <th className="n">{t("p3.doing.median_tokens")}</th>
                <th className="n">{t("p3.doing.with_issues")}</th>
                <th className="n">{t("p3.doing.approvals")}</th>
                <th className="n">👍 / 👎</th>
              </tr>
            </thead>
            <tbody>
              {doing.rows.map((r) => (
                <DoingTr key={r.fp ?? "unknown"} r={r} t={t} />
              ))}
            </tbody>
          </table>
        )}
        {newest && (
          <div className="flex items-start gap-3 mt-3 text-meta" data-testid="persona-doing-changed">
            <div className="flex-1 min-w-0">
              <b className="font-medium">{t("p3.doing.what_changed", { fp: newest.fp ?? "—" })}</b>{" "}
              <span className="text-muted">{changedLine(newest, t)}</span>
            </div>
            <button
              className="text-meta px-2.5 py-1 rounded-lg border border-line"
              disabled={!newest.changed || !newest.fp}
              data-testid="persona-doing-diff"
              onClick={async () => {
                if (!newest.changed || !newest.fp) return;
                if (diff?.fp === newest.fp) return setDiff(null);
                const d = await getPersonaVersionDiff(personaId, newest.changed.from, newest.fp, machineId).catch(() => null);
                if (d && !d.error) setDiff({ fp: newest.fp, files: d.files || [] });
              }}
            >
              {diff ? t("p3.doing.hide_diff") : t("p3.doing.show_diff")}
            </button>
          </div>
        )}
        {diff && (
          <div className="mt-2 space-y-2" data-testid="persona-doing-diff-body">
            {diff.files.map((f) => (
              <div key={f.path}>
                <div className="font-mono text-meta text-muted">{f.path}</div>
                <pre className="p3-diff">{f.diff || t("p3.doing.no_text_diff")}</pre>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="text-meta text-faint mt-1.5">{t("p3.doing.note", { n: doing.min_runs })}</div>
    </section>
  );
}

function DoingTr({ r, t }: { r: DoingRow; t: T }) {
  const from = r.first_seen != null ? shortDate(r.first_seen) : "—";
  const to = r.last_seen != null ? shortDate(r.last_seen) : "—";
  return (
    <tr data-testid="persona-doing-row">
      <td>
        <span className="font-mono">{r.unknown_version ? t("p3.doing.unknown_version") : `ⓕ${r.fp}`}</span>
        {r.current && <span className="p3-chip ok ml-1.5">{t("p3.doing.current")}</span>}
      </td>
      <td>{r.before_kept ? t("p3.doing.before", { date: to }) : `${from} – ${to}`}</td>
      <td className="n">
        {r.runs}
        {r.automation_runs > 0 && <span className="text-faint"> {t("p3.doing.automation_runs", { count: r.automation_runs })}</span>}
      </td>
      {r.too_few ? (
        <td className="n text-faint" colSpan={1}>{t("p3.doing.too_few")}</td>
      ) : (
        <td className="n">{(r.at_least ? "≥ " : "") + formatTokens(r.median_tokens ?? 0)}</td>
      )}
      <td className="n">{r.checked ? `${Math.round((r.with_warn / r.checked) * 100)}%` : "—"}</td>
      <td className="n">{r.approvals_per_run == null ? "—" : r.approvals_per_run.toFixed(1)}</td>
      <td className="n">{r.up || r.down ? `${r.up} / ${r.down}` : "— / —"}</td>
    </tr>
  );
}

/** "skill semgrep-scan 1.1 → 1.2 · instructions edited (+4 lines) · model unchanged". */
export function changedLine(r: DoingRow, t: T): string {
  const parts: string[] = [];
  if (!r.changed) parts.push(t("p3.doing.first_version"));
  else {
    const skills = new Map<string, VersionFileChange>();
    for (const f of r.changed.files) {
      if (f.skill) {
        if (!skills.has(f.skill) || f.path.endsWith("SKILL.md")) skills.set(f.skill, f);
        continue;
      }
      if (f.path === "manifest.md") {
        parts.push(f.kind === "edited"
          ? t("p3.doing.instructions_edited", { lines: (f.lines ?? 0) >= 0 ? `+${f.lines ?? 0}` : String(f.lines) })
          : t(`p3.doing.instructions_${f.kind}`));
      } else if (!f.path.startsWith("skills/")) {
        parts.push(t(`p3.doing.file_${f.kind}`, { path: f.path }));
      }
    }
    for (const f of r.changed.files) {
      if (f.path.startsWith("skills/") && !f.skill) {
        const name = f.path.split("/")[1];
        if (name && !skills.has(name)) skills.set(name, { ...f, skill: name });
      }
    }
    for (const [name, f] of skills) {
      if (f.from_version && f.to_version && f.from_version !== f.to_version)
        parts.push(t("p3.doing.skill_version", { skill: name, from: f.from_version, to: f.to_version }));
      else parts.push(t(`p3.doing.skill_${f.kind}`, { skill: name }));
    }
    if (!r.changed.files.length) parts.push(t("p3.doing.no_file_change"));
  }
  const m = r.model_line;
  if (m?.kind === "changed") parts.push(t("p3.doing.model_changed", { from: m.from, to: m.to }));
  else if (m?.kind === "several") parts.push(t("p3.doing.models", { models: (m.models || []).join(", ") }));
  else parts.push(t("p3.doing.model_unchanged"));
  return parts.join(" · ");
}
