import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getPersonaDefinition,
  getPersonaEditorOptions,
  previewPersonaDefinition,
  savePersonaDefinition,
  updatePersona,
  type Persona,
  type PersonaConsent,
  type PersonaDefinition,
  type PersonaEditorOptions,
} from "../api";
import { Icon } from "./Icon";
import { PersonaGlyph } from "./personaIcon";

const INPUT =
  "w-full rounded-lg border border-line bg-paper px-3 py-2 text-ui text-ink outline-none focus:border-accent disabled:opacity-50";
const BUTTON =
  "rounded-lg border border-line px-3 py-2 text-ui hover:border-lineStrong disabled:opacity-40";
const PRIMARY =
  "rounded-lg bg-accent px-4 py-2 text-ui text-white disabled:opacity-40";
const EMPTY: PersonaDefinition = {
  id: "",
  name: "",
  icon: "",
  tagline: "",
  description: "",
  system_prompt: "",
  tools: [],
  connectors: [],
  models: [],
  skills: [],
  mcp: [],
  recommends: [],
  requires_folder: false,
  subagents: false,
  scheduling: true,
  team: "",
  approval_guidance: "",
  default_permission_mode: "interactive",
  version: "1",
  group: "general",
};

export function PersonaEditor({
  personas,
  templateId,
  editingId,
  machineId,
  onClose,
  onSaved,
  onTry,
}: {
  personas: Persona[];
  templateId?: string;
  editingId?: string;
  machineId?: string | null;
  onClose: () => void;
  onSaved: (personas: Persona[]) => void;
  onTry?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<PersonaDefinition>({ ...EMPTY });
  const [source, setSource] = useState(editingId || templateId || "");
  const [options, setOptions] = useState<PersonaEditorOptions | null>(null);
  const [sourceSkills, setSourceSkills] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{
    consent: PersonaConsent;
    markdown: string;
  } | null>(null);
  const [saved, setSaved] = useState<Persona | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setOptions(null);
    setPreview(null);
    setError(null);
    Promise.all([
      getPersonaEditorOptions(machineId),
      source
        ? getPersonaDefinition(source, machineId)
        : Promise.resolve({ ...EMPTY }),
    ])
      .then(([catalog, definition]) => {
        if (!live) return;
        setOptions(catalog);
        setSourceSkills(definition.skills);
        if (source && !editingId) {
          const base = definition.id.slice(0, 53);
          let id = `${base}-copy`;
          for (let n = 2; personas.some((p) => p.id === id); n++)
            id = `${base}-copy-${n}`;
          setDraft({
            ...definition,
            id,
            name: t("persona_editor.copy_name", { name: definition.name }),
          });
        } else setDraft(definition);
      })
      .catch((e: unknown) => {
        if (live)
          setError(
            e instanceof Error ? e.message : t("persona_editor.load_failed"),
          );
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
    // The template is a snapshot; saving shouldn't reload or discard local edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, machineId, editingId, retry]);

  const change = <K extends keyof PersonaDefinition>(
    key: K,
    value: PersonaDefinition[K],
  ) => {
    setDraft((old) => ({ ...old, [key]: value }));
    setPreview(null);
    setError(null);
  };
  const toggle = (
    key: "tools" | "connectors" | "mcp" | "skills",
    id: string,
  ) => {
    const next = draft[key].includes(id)
      ? draft[key].filter((v) => v !== id)
      : [...draft[key], id];
    setDraft((old) => ({
      ...old,
      [key]: next,
      recommends:
        key === "connectors" || key === "mcp"
          ? old.recommends.filter(
              (r) =>
                r.kind !== (key === "connectors" ? "connector" : "mcp") ||
                next.includes(r.ref),
            )
          : old.recommends,
    }));
    setPreview(null);
    setError(null);
  };
  const request = {
    definition: draft,
    ...(editingId
      ? { editing_id: editingId }
      : source
        ? { template_id: source }
        : {}),
  };
  const act = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : t("persona_editor.save_failed"),
      );
    } finally {
      setBusy(false);
    }
  };
  const textField = (
    key:
      | "name"
      | "id"
      | "icon"
      | "tagline"
      | "description"
      | "system_prompt"
      | "approval_guidance"
      | "version",
    rows?: number,
  ) => (
    <div className="block space-y-1.5">
      <label
        htmlFor={`persona-editor-${key}`}
        className="text-meta font-medium text-muted"
      >
        {t(`persona_editor.${key}`)}
        {["name", "id", "system_prompt"].includes(key) ? " *" : ""}
      </label>
      {rows ? (
        <textarea
          id={`persona-editor-${key}`}
          className={
            INPUT +
            (key === "system_prompt"
              ? " font-mono leading-relaxed resize-y"
              : " resize-y")
          }
          rows={rows}
          value={draft[key]}
          onChange={(e) => change(key, e.target.value)}
          required={key === "system_prompt"}
        />
      ) : (
        <input
          id={`persona-editor-${key}`}
          aria-describedby={key === "id" ? "persona-editor-id-hint" : undefined}
          className={INPUT}
          value={draft[key]}
          onChange={(e) => change(key, e.target.value)}
          required={key === "name" || key === "id"}
          disabled={key === "id" && !!editingId}
          maxLength={key === "id" ? 64 : undefined}
          pattern={key === "id" ? "[a-z0-9][a-z0-9_-]{0,63}" : undefined}
        />
      )}
      {key === "id" && (
        <span
          id="persona-editor-id-hint"
          className="block text-meta text-faint"
        >
          {t("persona_editor.id_hint")}
        </span>
      )}
    </div>
  );
  const choices = (
    key: "connectors" | "mcp" | "skills",
    items: { id: string; name: string }[],
  ) => {
    const all = [
      ...items,
      ...draft[key]
        .filter((id) => !items.some((c) => c.id === id))
        .map((id) => ({ id, name: id })),
    ];
    return (
      <div className="flex flex-wrap gap-2">
        {all.map((c) => (
          <label
            key={c.id}
            className={
              "inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-ui cursor-pointer " +
              (draft[key].includes(c.id)
                ? "border-accent bg-accentSoft"
                : "border-line bg-paper")
            }
          >
            <input
              type="checkbox"
              checked={draft[key].includes(c.id)}
              onChange={() => toggle(key, c.id)}
            />
            {c.name}
          </label>
        ))}
        {!all.length && (
          <p className="text-meta text-faint">
            {t(`persona_editor.${key}_empty`)}
          </p>
        )}
      </div>
    );
  };

  return (
    <section
      className="mt-5"
      aria-labelledby="persona-editor-title"
      data-testid="persona-editor"
    >
      <div className="flex items-start justify-between gap-4 border-b border-line pb-5 mb-6">
        <div>
          <div className="text-label uppercase tracking-widest text-faint mb-1">
            {t("persona_editor.eyebrow")}
          </div>
          <h2
            id="persona-editor-title"
            ref={heading}
            tabIndex={-1}
            className="text-xl font-semibold outline-none"
          >
            {t(editingId ? "persona_editor.edit" : "persona_editor.create")}
          </h2>
          <p className="text-ui text-muted mt-1">{t("persona_editor.intro")}</p>
        </div>
        <button
          type="button"
          className={BUTTON}
          disabled={busy}
          onClick={onClose}
        >
          {t(saved ? "persona_editor.done" : "persona_editor.cancel")}
        </button>
      </div>
      {error && (
        <div
          role="alert"
          className="rounded-lg border border-warnInk/30 bg-warnSoft p-3 text-ui text-warnInk mb-4"
        >
          {error}
        </div>
      )}
      {loading ? (
        <p role="status" className="text-ui text-muted">
          {t("persona_editor.loading")}
        </p>
      ) : !options ? (
        <button className={BUTTON} onClick={() => setRetry((v) => v + 1)}>
          {t("persona_editor.retry")}
        </button>
      ) : saved ? (
        <div
          className="rounded-xl2 border border-line bg-panel p-6 space-y-4"
          data-testid="persona-editor-saved"
        >
          <Icon name="user" size={24} className="text-accent" />
          <h3 className="text-lg font-semibold">
            {t("persona_editor.saved", { name: saved.name })}
          </h3>
          <p className="text-ui text-muted">
            {t(
              saved.enabled
                ? "persona_editor.ready"
                : "persona_editor.enable_hint",
            )}
          </p>
          <div className="flex flex-wrap gap-3">
            {!saved.enabled && (
              <button
                className={PRIMARY}
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const result = await updatePersona(
                      saved.id,
                      { enabled: true, surfaced: draft.team !== "worker" },
                      machineId,
                    );
                    if (!result.ok)
                      throw new Error(
                        result.error || t("persona_editor.save_failed"),
                      );
                    const next = result.personas?.find(
                      (p) => p.id === saved.id,
                    );
                    if (!next) throw new Error(t("persona_editor.save_failed"));
                    setSaved(next);
                    onSaved(result.personas!);
                  })
                }
              >
                {t("persona_editor.enable")}
              </button>
            )}
            {onTry && draft.team !== "worker" && (
              <button
                className={BUTTON}
                disabled={busy || !saved.enabled}
                onClick={() => onTry(saved.id)}
              >
                {t("persona_editor.try")}
              </button>
            )}
            {draft.team === "worker" && (
              <p className="text-meta text-faint">
                {t("persona_editor.worker_trial")}
              </p>
            )}
          </div>
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () =>
              setPreview(await previewPersonaDefinition(request, machineId)),
            );
          }}
        >
          <fieldset disabled={busy} className="min-w-0">
            {!editingId && (
              <label className="block mb-6 space-y-1.5">
                <span className="text-meta font-medium text-muted">
                  {t("persona_editor.template")}
                </span>
                <select
                  className={INPUT}
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                >
                  <option value="">{t("persona_editor.blank")}</option>
                  {personas.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                {source && (
                  <span className="block text-meta text-faint">
                    {t("persona_editor.template_hint")}
                  </span>
                )}
              </label>
            )}
            <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_240px] gap-7 items-start">
              <div className="space-y-6 min-w-0">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {textField("name")}
                  {textField("id")}
                </div>
                <div className="grid grid-cols-[90px_minmax(0,1fr)] gap-4">
                  {textField("icon")}
                  {textField("tagline")}
                </div>
                {textField("description", 2)}
                {textField("system_prompt", 14)}
                <section
                  className="space-y-3"
                  aria-label={t("persona_editor.tools")}
                >
                  <h3 className="text-ui font-semibold">
                    {t("persona_editor.tools")}
                  </h3>
                  <p className="text-meta text-muted">
                    {t("persona_editor.tools_hint")}
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {options.capabilities.map((cap) => (
                      <label
                        key={cap.id}
                        className={
                          "flex items-start gap-3 rounded-lg border p-3 cursor-pointer " +
                          (draft.tools.includes(cap.id)
                            ? "border-accent bg-accentSoft"
                            : "border-line bg-paper")
                        }
                      >
                        <input
                          className="mt-1"
                          type="checkbox"
                          checked={draft.tools.includes(cap.id)}
                          onChange={() => toggle("tools", cap.id)}
                        />
                        <span>
                          <span className="block text-ui font-medium">
                            {t(`persona_editor.capabilities.${cap.id}.name`, { defaultValue: cap.name })}
                          </span>
                          <span className="block text-meta text-muted mt-0.5">
                            {t(`persona_editor.capabilities.${cap.id}.description`, { defaultValue: cap.description })}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                </section>
                <section className="space-y-3">
                  <h3 className="text-ui font-semibold">
                    {t("persona_editor.connectors")}
                  </h3>
                  {choices("connectors", options.connectors)}
                </section>
                <details className="rounded-xl2 border border-line p-4">
                  <summary className="text-ui font-semibold cursor-pointer">
                    {t("persona_editor.advanced")}
                  </summary>
                  <div className="space-y-5 pt-5">
                    <div className="grid sm:grid-cols-2 gap-4">
                      <label className="space-y-1.5">
                        <span className="block text-meta text-muted">
                          {t("persona_editor.group")}
                        </span>
                        <select
                          className={INPUT}
                          value={draft.group}
                          onChange={(e) => change("group", e.target.value)}
                        >
                          {["general", "engineering", "security"].map((v) => (
                            <option key={v} value={v}>
                              {t(`persona_editor.group_${v}`)}
                            </option>
                          ))}
                        </select>
                      </label>
                      {textField("version")}
                    </div>
                    <label className="block space-y-1.5">
                      <span className="block text-meta text-muted">
                        {t("persona_editor.mode")}
                      </span>
                      <select
                        className={INPUT}
                        value={draft.default_permission_mode}
                        onChange={(e) =>
                          change("default_permission_mode", e.target.value)
                        }
                      >
                        {options.modes.map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                      <span className="block text-meta text-faint">
                        {t("persona_editor.mode_hint")}
                      </span>
                    </label>
                    {(
                      ["requires_folder", "subagents", "scheduling"] as const
                    ).map((key) => (
                      <label
                        key={key}
                        className="flex items-start gap-3 text-ui"
                      >
                        <input
                          className="mt-1"
                          type="checkbox"
                          checked={draft[key]}
                          onChange={(e) => change(key, e.target.checked)}
                        />
                        <span>
                          {t(`persona_editor.${key}`)}
                          <span className="block text-meta text-faint">
                            {t(`persona_editor.${key}_hint`)}
                          </span>
                        </span>
                      </label>
                    ))}
                    <label className="block space-y-1.5">
                      <span className="text-meta text-muted">
                        {t("persona_editor.team")}
                      </span>
                      <select
                        className={INPUT}
                        value={draft.team}
                        onChange={(e) => change("team", e.target.value)}
                      >
                        {["", "lead", "worker"].map((v) => (
                          <option key={v} value={v}>
                            {t(`persona_editor.team_${v || "solo"}`)}
                          </option>
                        ))}
                      </select>
                    </label>
                    {textField("approval_guidance", 3)}
                    <label className="block space-y-1.5">
                      <span className="text-meta text-muted">
                        {t("persona_editor.models")}
                      </span>
                      <textarea
                        className={INPUT + " font-mono"}
                        rows={3}
                        value={draft.models.join("\n")}
                        onChange={(e) =>
                          change("models", e.target.value.split("\n"))
                        }
                        onBlur={() =>
                          change("models", [
                            ...new Set(
                              draft.models.map((v) => v.trim()).filter(Boolean),
                            ),
                          ])
                        }
                      />
                      <span className="block text-meta text-faint">
                        {t("persona_editor.models_hint")}
                      </span>
                    </label>
                    <section className="space-y-2">
                      <h4 className="text-ui font-medium">
                        {t("persona_editor.skills")}
                      </h4>
                      {choices(
                        "skills",
                        sourceSkills.map((id) => ({ id, name: id })),
                      )}
                    </section>
                    <section className="space-y-2">
                      <h4 className="text-ui font-medium">
                        {t("persona_editor.mcp")}
                      </h4>
                      <p className="text-meta text-faint">
                        {t("persona_editor.mcp_hint")}
                      </p>
                      {choices(
                        "mcp",
                        options.mcp.map((id) => ({ id, name: id })),
                      )}
                    </section>
                    <section className="space-y-3">
                      <h4 className="text-ui font-medium">
                        {t("persona_editor.recommendations")}
                      </h4>
                      <p className="text-meta text-faint">
                        {t("persona_editor.recommendations_hint")}
                      </p>
                      {[
                        ...draft.connectors.map((ref) => ({
                          kind: "connector",
                          ref,
                        })),
                        ...draft.mcp.map((ref) => ({ kind: "mcp", ref })),
                      ].map(({ kind, ref }) => {
                        const rec = draft.recommends.find(
                          (r) => r.kind === kind && r.ref === ref,
                        );
                        const setRec = (
                          tier: string,
                          reason = rec?.reason || "",
                        ) =>
                          change("recommends", [
                            ...draft.recommends.filter(
                              (r) => r.kind !== kind || r.ref !== ref,
                            ),
                            ...(tier ? [{ kind, ref, tier, reason }] : []),
                          ]);
                        return (
                          <div
                            key={kind + ref}
                            className="rounded-lg border border-line p-3 space-y-2"
                          >
                            <div className="flex items-center gap-3">
                              <span className="text-ui flex-1">
                                {ref}{" "}
                                <span className="text-meta text-faint">
                                  {kind}
                                </span>
                              </span>
                              <select
                                aria-label={t(
                                  "persona_editor.recommendation_for",
                                  { name: ref },
                                )}
                                className={INPUT + " max-w-[180px]"}
                                value={rec?.tier || ""}
                                onChange={(e) => setRec(e.target.value)}
                              >
                                {["", "core", "optional"].map((v) => (
                                  <option key={v} value={v}>
                                    {t(`persona_editor.tier_${v || "none"}`)}
                                  </option>
                                ))}
                              </select>
                            </div>
                            {rec && (
                              <input
                                aria-label={t("persona_editor.reason_for", {
                                  name: ref,
                                })}
                                className={INPUT}
                                value={rec.reason}
                                onChange={(e) =>
                                  setRec(rec.tier, e.target.value)
                                }
                                placeholder={t("persona_editor.reason")}
                              />
                            )}
                          </div>
                        );
                      })}
                    </section>
                  </div>
                </details>
              </div>
              <aside
                className="rounded-xl2 border border-line bg-panel p-5 xl:sticky xl:top-4 space-y-4"
                aria-label={t("persona_editor.summary")}
              >
                <div className="text-label uppercase tracking-widest text-faint">
                  {t("persona_editor.summary")}
                </div>
                  <PersonaGlyph icon={draft.icon} folderScoped={draft.requires_folder} size={28} />
                <div>
                  <div className="text-lg font-semibold break-words">
                    {draft.name || t("persona_editor.untitled")}
                  </div>
                  <p className="text-meta text-muted mt-1 break-words">
                    {draft.tagline}
                  </p>
                </div>
                <p className="text-ui text-muted break-words whitespace-pre-wrap">
                  {draft.description}
                </p>
                <div className="border-t border-line pt-3 text-meta text-muted space-y-2">
                  <p>
                    {t("persona_editor.tool_count", {
                      count: draft.tools.length,
                    })}
                  </p>
                  <p>
                    {t("persona_editor.connection_count", {
                      count: draft.connectors.length + draft.mcp.length,
                    })}
                  </p>
                  <p>
                    {t(
                      draft.requires_folder
                        ? "persona_editor.workspace_folder"
                        : "persona_editor.workspace_scratch",
                    )}
                  </p>
                </div>
                <p className="text-meta text-faint">
                  {t("persona_editor.consent_hint")}
                </p>
              </aside>
            </div>
            {preview && (
              <section
                className="mt-6 rounded-xl2 border border-accent/40 bg-accentSoft p-5 space-y-3"
                aria-label={t("persona_editor.review")}
                data-testid="persona-editor-review"
              >
                <h3 className="font-semibold text-ui">
                  {t("persona_editor.review")}
                </h3>
                <p className="text-ui">
                  {t("persona_editor.risks")}:{" "}
                  {preview.consent.risk
                    .map((r) => t(`personas.risk.${r}`))
                    .join(" · ") || t("persona_editor.none")}
                </p>
                <p className="text-ui">
                  {t("persona_editor.connectors")}:{" "}
                  {draft.connectors.join(" · ") || t("persona_editor.none")}
                </p>
                <p className="text-ui">
                  {t("persona_editor.mcp")}:{" "}
                  {draft.mcp.join(" · ") || t("persona_editor.mcp_inherit")}
                </p>
                <p className="text-ui">
                  {t("persona_editor.skills")}:{" "}
                  {draft.skills.join(" · ") || t("persona_editor.none")}
                </p>
                <p className="text-ui">
                  {t("persona_editor.team")}:{" "}
                  {t(`persona_editor.team_${draft.team || "solo"}`)}
                </p>
                <p className="text-ui">
                  {t("persona_editor.mode")}: {draft.default_permission_mode}
                </p>
                <p className="text-ui">
                  {t("persona_editor.execution_traits")}:{" "}
                  {[
                    ...(draft.subagents ? [t("persona_editor.subagents")] : []),
                    ...(draft.scheduling
                      ? [t("persona_editor.scheduling")]
                      : []),
                  ].join(" · ") || t("persona_editor.none")}
                </p>
                {preview.consent.replaces?.capabilities_grew && (
                  <p className="text-ui text-warnInk">
                    {t("persona_editor.reconsent")}
                  </p>
                )}
                {editingId && (
                  <p className="text-meta text-muted">
                    {t("persona_editor.live_sessions")}
                  </p>
                )}
                <details>
                  <summary className="text-meta cursor-pointer">
                    {t("persona_editor.manifest")}
                  </summary>
                  <pre className="text-meta whitespace-pre-wrap break-words max-h-80 overflow-auto mt-3 rounded-lg bg-paper p-3">
                    {preview.markdown}
                  </pre>
                </details>
              </section>
            )}
            <div className="flex flex-wrap items-center gap-3 mt-6 border-t border-line pt-4">
              {preview ? (
                <button
                  type="button"
                  className={PRIMARY}
                  onClick={() =>
                    void act(async () => {
                      const result = await savePersonaDefinition(
                        request,
                        machineId,
                      );
                      const persona = result.personas.find(
                        (p) => p.id === draft.id,
                      );
                      if (!persona)
                        throw new Error(t("persona_editor.save_failed"));
                      setSaved(persona);
                      onSaved(result.personas);
                    })
                  }
                >
                  {busy ? t("persona_editor.saving") : t("persona_editor.save")}
                </button>
              ) : (
                <button type="submit" className={PRIMARY}>
                  {busy
                    ? t("persona_editor.checking")
                    : t("persona_editor.preview")}
                </button>
              )}
              <span className="text-meta text-faint">
                {t("persona_editor.local_hint")}
              </span>
            </div>
          </fieldset>
        </form>
      )}
    </section>
  );
}
