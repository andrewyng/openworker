// The staffing gate (agent teams, UX-030; worker-connector-grants spec §2, §6): a lead
// proposes its worker roster. Visible layer = the decisions (who — by callname — on what
// model, why), plus per worker the connectors it may use. A worker's declared connectors
// are its DEFAULT set: the lead's suggestion there arrives ticked, with its reason.
// Beyond the default set sits a second group — shown when the lead proposed something
// there (quoting the human's own words) or when the human adds one from everything else
// connected on the machine. The human's final ticks are what the worker gets.
// Approvals FOLLOW THE LEAD: the card states the lead's real mode (live from the
// composer inline; from the gate payload in the Inbox) and has no control of its own.
// Approving grants the lead create/assign/steer for this board — standing, revocable —
// and PRE-SPAWNS the worker sessions. The chat checkbox is the USER's call (default OFF,
// ⓘ per mock); no in-card reply surface: editing happens by replying.
// Models: when the server lists what can run on this machine (`runnable_models`), each
// worker's model is a quiet picker on line one — recommended models first, the rest
// after. A worker whose persona recommends nothing runnable arrives on the fallback with
// a warning glyph; the human's final pick per worker rides the approval. An older
// server sends no list, and the model stays plain text.
// Large teams (owner-ruled 2026-09-17): workers that share a role collapse into ONE line —
// a count, one model and one set of connector ticks for the whole role. "Show workers"
// opens the ordinary per-worker rows for a human who wants to split the role (progressive
// disclosure). A role whose workers differ says so on its line ("Mixed", "11 of 12")
// rather than opening by itself. State and the answer stay per worker: grouping is a view.
import { useRef, useState, type ReactNode } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { TeamMemberDecision } from "../api";
import type { Item } from "../types";
import { modeLabel } from "./Composer";
import { connectorLabel } from "./ConnectorRequestCard";
import { Icon } from "./Icon";
import { modelHover, modelName } from "../modelNames";
import { ProposalActions } from "./ProposalParts";

// Past this length the lead's note clamps to two lines with More/Less — the roster and
// the connector decisions are the card's subject, not the note.
const NOTE_CLAMP_CHARS = 140;

const MIXED = "__mixed__";

const WarnGlyph = () => (
  <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
    <path
      d="M8 2.2 14.2 13H1.8Z"
      fill="var(--warn-soft)"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
    />
    <path
      d="M8 6.3v3.4"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
    <circle cx="8" cy="11.4" r="0.85" fill="currentColor" />
  </svg>
);

export function TeamRequestCard({
  item,
  leadMode,
  modelLabels,
  onRespond,
}: {
  item: Extract<Item, { kind: "teamreq" }>;
  // The lead session's CURRENT approval mode (App state) — rendered, never edited here.
  leadMode?: string;
  // Curated display names from /v1/settings (when the caller has them).
  modelLabels?: Record<string, string>;
  onRespond: (
    approved: boolean,
    feedback?: string,
    enableChat?: boolean,
    members?: TeamMemberDecision[],
  ) => void;
}) {
  const { t } = useTranslation();
  const [chat, setChat] = useState(!!item.enable_chat);
  const [noteOpen, setNoteOpen] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [guidance, setGuidance] = useState(() => item.members.map(m => m.approval_guidance ?? ""));
  const rosterRef = useRef<HTMLDivElement>(null);
  const [names, setNames] = useState(() =>
    item.members.map((m) => m.name || ""),
  );
  const normalizedNames = names.map((name) => name.trim().toLowerCase());
  const invalidNames =
    !!item.groups?.length &&
    (normalizedNames.some(
      (name) =>
        !/^[a-z0-9][a-z0-9._-]{0,23}$/.test(name) ||
        ["lead", "user", "board"].includes(name),
    ) ||
      new Set(normalizedNames).size !== names.length);
  const [adding, setAdding] = useState<Record<number, boolean>>({});
  const other = item.other_connected ?? [];
  const offerFor = (persona: string) => item.offer?.[persona] ?? [];
  // What a worker may end up holding: its default set, or anything else connected here.
  const allowedFor = (persona: string) => {
    const offer = offerFor(persona);
    return [...offer, ...other.filter((c) => !offer.includes(c))];
  };
  // The lead's suggestions start ticked — minus anything not connected on this machine.
  const [ticked, setTicked] = useState<Record<number, string[]>>(() =>
    Object.fromEntries(
      item.members.map((m, i) => {
        const allowed = allowedFor(m.persona);
        return [
          i,
          [...new Set(m.connectors ?? [])].filter((c) => allowed.includes(c)),
        ];
      }),
    ),
  );
  // The "beyond" group per worker: the lead's out-of-default suggestions, then the
  // human's additions. An entry stays listed once shown, so unticking never hides it.
  const [beyond, setBeyond] = useState<Record<number, string[]>>(() =>
    Object.fromEntries(
      item.members.map((m, i) => {
        const offer = offerFor(m.persona);
        return [
          i,
          [...new Set(m.connectors ?? [])].filter(
            (c) => !offer.includes(c) && other.includes(c),
          ),
        ];
      }),
    ),
  );
  const toggle = (i: number, c: string) =>
    setTicked((cur) => {
      const have = cur[i] ?? [];
      return {
        ...cur,
        [i]: have.includes(c) ? have.filter((x) => x !== c) : [...have, c],
      };
    });
  const addBeyond = (i: number, c: string) => {
    setBeyond((cur) => ({ ...cur, [i]: [...(cur[i] ?? []), c] }));
    setTicked((cur) => ({
      ...cur,
      [i]: [...(cur[i] ?? []).filter((x) => x !== c), c],
    }));
  };
  // Set one connector for several workers at once (a role line). Turning on a connector
  // outside a worker's usual set lists it in that worker's "beyond" group too.
  const setForWorkers = (idxs: number[], c: string, on: boolean) => {
    setTicked((cur) => {
      const next = { ...cur };
      for (const i of idxs) {
        const have = (next[i] ?? []).filter((x) => x !== c);
        next[i] = on ? [...have, c] : have;
      }
      return next;
    });
    if (on)
      setBeyond((cur) => {
        const next = { ...cur };
        for (const i of idxs)
          if (
            !offerFor(item.members[i].persona).includes(c) &&
            !(next[i] ?? []).includes(c)
          )
            next[i] = [...(next[i] ?? []), c];
        return next;
      });
  };
  // Roles in order of first appearance, each with the roster indexes of its workers.
  const groups: { persona: string; idxs: number[] }[] = [];
  item.members.forEach((m, i) => {
    const g = groups.find((x) => x.persona === m.persona);
    if (g) g.idxs.push(i);
    else groups.push({ persona: m.persona, idxs: [i] });
  });
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [groupAdding, setGroupAdding] = useState<Record<string, boolean>>({});
  const runnable = Array.isArray(item.runnable_models)
    ? item.runnable_models
    : null;
  const recommendedFor = (persona: string) => {
    const rec = item.model_options?.[persona];
    return Array.isArray(rec) ? rec : [];
  };
  // The model's NAME only; provider and raw id go in the hover text.
  const serverLabels = Object.fromEntries(
    (runnable ?? []).map((r) => [r.id, r.label]),
  );
  const labels = { ...serverLabels, ...(modelLabels ?? {}) };
  const modelLabelOf = (id: string) => modelName(id, labels);
  // Each worker's current model pick — starts on what the server resolved for it.
  const [picked, setPicked] = useState<Record<number, string>>(() =>
    Object.fromEntries(
      item.members.map((m, i) => [
        i,
        m.resolved_model || m.model || item.lead_model || "",
      ]),
    ),
  );
  const decisions = (): TeamMemberDecision[] =>
    item.members.map((m, i) => {
      const allowed = allowedFor(m.persona);
      return {
        persona: m.persona,
        ...(names[i] ? { name: names[i].trim() } : {}),
        connectors: (ticked[i] ?? []).filter((c) => allowed.includes(c)),
        ...(guidance[i] || m.approval_guidance !== undefined ? { approval_guidance: guidance[i] } : {}),
        ...(runnable && picked[i] ? { model: picked[i] } : {}),
      };
    });
  const mode = leadMode ?? item.lead_mode;
  const note = item.note || "";
  const noteLong = note.length > NOTE_CLAMP_CHARS;
  const connectorBox = (i: number, c: string, reason?: string) => (
    <label key={c} className="teamreq-connector">
      <input
        type="checkbox"
        data-testid={`teamreq-connector-${i}-${c}`}
        checked={(ticked[i] ?? []).includes(c)}
        onChange={() => toggle(i, c)}
      />
      <span>{connectorLabel(c)}</span>
      {reason && <span className="teamreq-connector-reason">{reason}</span>}
    </label>
  );
  const modelOptions = (persona: string, current: string) => {
    const recommended = runnable ? recommendedFor(persona) : [];
    const others = (runnable ?? [])
      .map((r) => r.id)
      .filter((id) => !recommended.includes(id));
    return (
      <>
        {current !== MIXED &&
          !recommended.includes(current) &&
          !others.includes(current) && (
            <option value={current}>
              {current ? modelLabelOf(current) : t("team.default_model")}
            </option>
          )}
        {recommended.length > 0 && (
          <optgroup label={t("team.models_recommended")}>
            {recommended.map((id) => (
              <option key={id} value={id}>
                {modelLabelOf(id)}
              </option>
            ))}
          </optgroup>
        )}
        {others.length > 0 && (
          <optgroup label={t("team.models_on_machine")}>
            {others.map((id) => (
              <option key={id} value={id}>
                {modelLabelOf(id)}
              </option>
            ))}
          </optgroup>
        )}
      </>
    );
  };
  const warningOf = (i: number) => {
    const m = item.members[i];
    return runnable &&
      m.model_warning &&
      (picked[i] ?? "") === (m.resolved_model ?? "")
      ? m.model_warning
      : "";
  };
  const memberRow = (i: number): ReactNode => {
    const m = item.members[i];
    const offer = offerFor(m.persona);
    const extra = beyond[i] ?? [];
    const addable = other.filter(
      (c) => !offer.includes(c) && !extra.includes(c),
    );
    const reasons = m.connector_reasons ?? {};
    const current = picked[i] ?? "";
    const recommended = runnable ? recommendedFor(m.persona) : [];
    const warn = warningOf(i);
    const offList =
      !!runnable && recommended.length > 0 && !recommended.includes(current);
    return (
      <div className="teamreq-row" key={i} data-testid={`teamreq-row-${i}`}>
        <span className="teamreq-diamond">◆</span>
        <span className="teamreq-body">
          <span className="teamreq-who">
            {m.name && <b className="teamreq-name">{m.name}</b>}
            {m.name ? " — " : ""}
            <code>{m.persona}</code>
            {runnable ? (
              <>
                <span className="teamreq-model"> · </span>
                <span className="teamreq-model-pick">
                  <select
                    className="teamreq-model-select"
                    data-testid={`teamreq-model-${i}`}
                    aria-label={t("team.model_for", {
                      who: m.name || m.persona,
                    })}
                    title={modelHover(current, labels)}
                    value={current}
                    onChange={(e) =>
                      setPicked((cur) => ({ ...cur, [i]: e.target.value }))
                    }
                  >
                    {modelOptions(m.persona, current)}
                  </select>
                </span>
                {warn && (
                  <span
                    className="teamreq-model-warn"
                    data-testid={`teamreq-model-warn-${i}`}
                    role="img"
                    aria-label={warn}
                    title={warn}
                  >
                    <WarnGlyph />
                  </span>
                )}
              </>
            ) : (
              m.model && (
                <span
                  className="teamreq-model"
                  title={modelHover(m.model, labels)}
                >
                  {" "}
                  · {modelLabelOf(m.model)}
                </span>
              )
            )}
          </span>
          {offList && (
            <span
              className="teamreq-model-note"
              data-testid={`teamreq-model-note-${i}`}
            >
              {t("team.model_off_list_worker")}
            </span>
          )}
          {m.reason && <span className="teamreq-reason">{m.reason}</span>}
          {!!item.groups?.length && (
            <label className="proposal-name">
              {t("proposal.worker_name")}
              <input
                value={names[i]}
                onChange={(e) =>
                  setNames((cur) =>
                    cur.map((name, index) =>
                      index === i ? e.target.value : name,
                    ),
                  )
                }
              />
            </label>
          )}
          {!!m.item_ids?.length && (
            <span className="proposal-responsibilities">
              <span className="proposal-label">
                {t("proposal.planned_responsibilities")}
              </span>
              {m.item_ids.map((id) => (
                <span key={id}>
                  {item.planned_items?.find((task) => task.id === id)?.title ||
                    `#${id}`}
                </span>
              ))}
            </span>
          )}
          <span className="teamreq-dials">
            {offer.length > 0 ? (
              <span className="teamreq-connectors">
                <span className="teamreq-dial-label">
                  {t("persona.connectors")}
                </span>
                {offer.map((c) => connectorBox(i, c, reasons[c]))}
              </span>
            ) : extra.length === 0 && other.length === 0 ? (
              <span className="teamreq-connectors">
                <span className="teamreq-dial-label">
                  {t("persona.connectors")}
                </span>
                <span className="teamreq-none">{t("team.none_available")}</span>
              </span>
            ) : null}
            {extra.length > 0 && (
              <span
                className="teamreq-connectors teamreq-beyond"
                data-testid={`teamreq-beyond-${i}`}
              >
                <span className="teamreq-dial-label">
                  {t("team.beyond_worker")}
                </span>
                {extra.map((c) => connectorBox(i, c, reasons[c]))}
              </span>
            )}
            {addable.length > 0 && (
              <button
                className="teamreq-add"
                data-testid={`teamreq-add-${i}`}
                aria-expanded={!!adding[i]}
                onClick={() => setAdding((s) => ({ ...s, [i]: !s[i] }))}
              >
                {t("team.add_connector")}
              </button>
            )}
            {adding[i] && addable.length > 0 && (
              <span
                className="teamreq-connectors"
                data-testid={`teamreq-addlist-${i}`}
              >
                {addable.map((c) => (
                  <label key={c} className="teamreq-connector">
                    <input
                      type="checkbox"
                      data-testid={`teamreq-connector-${i}-${c}`}
                      checked={false}
                      onChange={() => addBeyond(i, c)}
                    />
                    <span>{connectorLabel(c)}</span>
                  </label>
                ))}
              </span>
            )}
          </span>
        </span>
      </div>
    );
  };
  // One line for a whole role. Its controls set every worker of the role at once.
  const groupRow = (g: { persona: string; idxs: number[] }): ReactNode => {
    const { persona, idxs } = g;
    const n = idxs.length;
    const offer = offerFor(persona);
    const isOpen = !!open[persona];
    const names = idxs.map((i) => item.members[i].name || "").filter(Boolean);
    const reasonsSet = new Set(idxs.map((i) => item.members[i].reason || ""));
    const sharedReason = reasonsSet.size === 1 ? [...reasonsSet][0] : "";
    // Model: one value when the role agrees, otherwise a count per model.
    const models = idxs.map((i) => picked[i] ?? "");
    const uniform = models.every((x) => x === models[0]);
    const tally = [...new Set(models)].map((id) => {
      const count = models.filter((x) => x === id).length;
      const model = modelLabelOf(id);
      return model
        ? t("team.tally_model", { n: count, model })
        : t("team.tally_default", { n: count });
    });
    const tallyText = tally.join(t("team.list_separator"));
    const recommended = runnable ? recommendedFor(persona) : [];
    const offList =
      !!runnable &&
      uniform &&
      recommended.length > 0 &&
      !recommended.includes(models[0]);
    const warned = idxs.filter((i) => warningOf(i));
    const warn = warned.length
      ? warned.length < n
        ? t("team.warning_partial", {
            warning: warningOf(warned[0]),
            have: warned.length,
            n,
          })
        : warningOf(warned[0])
      : "";
    // Connectors: the role's usual set, then anything beyond it that any worker holds.
    const extra = [...new Set(idxs.flatMap((i) => beyond[i] ?? []))];
    const addable = other.filter(
      (c) => !offer.includes(c) && !extra.includes(c),
    );
    const holders = (c: string) =>
      idxs.filter((i) => (ticked[i] ?? []).includes(c));
    const sharedConnectorReason = (c: string) => {
      const rs = new Set(
        holders(c).map((i) => item.members[i].connector_reasons?.[c] || ""),
      );
      return rs.size === 1 ? [...rs][0] : "";
    };
    const groupBox = (c: string) => {
      const have = holders(c).length;
      const reason = sharedConnectorReason(c);
      return (
        <label key={c} className="teamreq-connector">
          <input
            type="checkbox"
            data-testid={`teamreq-group-connector-${persona}-${c}`}
            checked={have === n}
            ref={(el) => {
              if (el) el.indeterminate = have > 0 && have < n;
            }}
            onChange={() => setForWorkers(idxs, c, have !== n)}
          />
          <span>{connectorLabel(c)}</span>
          {have > 0 && have < n && (
            <span
              className="teamreq-partial"
              data-testid={`teamreq-group-partial-${persona}-${c}`}
            >
              {t("team.partial", { have, n })}
            </span>
          )}
          {reason && <span className="teamreq-connector-reason">{reason}</span>}
        </label>
      );
    };
    // Sits at the end of the last connectors line, so a role stays three lines tall.
    const addButton = addable.length > 0 && (
      <button
        className="teamreq-add teamreq-add-inline"
        data-testid={`teamreq-group-add-${persona}`}
        aria-expanded={!!groupAdding[persona]}
        onClick={() =>
          setGroupAdding((cur) => ({ ...cur, [persona]: !cur[persona] }))
        }
      >
        {t("team.add_another")}
      </button>
    );
    return (
      <div
        className="teamreq-group"
        key={persona}
        data-testid={`teamreq-group-${persona}`}
      >
        <div className="teamreq-row teamreq-group-row">
          <span className="teamreq-diamond">◆</span>
          <span className="teamreq-body">
            <span className="teamreq-who">
              <code>{persona}</code>
              <b className="teamreq-count">× {n}</b>
              {runnable ? (
                <>
                  <span className="teamreq-model">·</span>
                  <span className="teamreq-model-pick">
                    <select
                      className="teamreq-model-select"
                      data-testid={`teamreq-group-model-${persona}`}
                      aria-label={t("team.model_for_role", { n, persona })}
                      title={
                        uniform ? modelHover(models[0], labels) : tallyText
                      }
                      value={uniform ? models[0] : MIXED}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v !== MIXED)
                          setPicked((cur) => ({
                            ...cur,
                            ...Object.fromEntries(idxs.map((i) => [i, v])),
                          }));
                      }}
                    >
                      {!uniform && (
                        <option value={MIXED} disabled>
                          {t("team.mixed_models", { n: tally.length })}
                        </option>
                      )}
                      {modelOptions(persona, uniform ? models[0] : MIXED)}
                    </select>
                  </span>
                  {warn && (
                    <span
                      className="teamreq-model-warn"
                      data-testid={`teamreq-group-warn-${persona}`}
                      role="img"
                      aria-label={warn}
                      title={warn}
                    >
                      <WarnGlyph />
                    </span>
                  )}
                </>
              ) : null}
              <span className="spacer" />
              <button
                className="teamreq-add teamreq-group-toggle"
                data-testid={`teamreq-group-toggle-${persona}`}
                aria-expanded={isOpen}
                onClick={() =>
                  setOpen((cur) => ({ ...cur, [persona]: !cur[persona] }))
                }
              >
                {isOpen ? t("team.hide_workers") : t("team.show_workers")}
              </button>
            </span>
            {offList && (
              <span className="teamreq-model-note">
                {t("team.model_off_list_role")}
              </span>
            )}
            {!uniform && (
              <span
                className="teamreq-model-note"
                data-testid={`teamreq-group-tally-${persona}`}
              >
                {tallyText}
              </span>
            )}
            <span className="teamreq-reason">
              {sharedReason ||
                (names.length > 3
                  ? t("team.names_and_more", {
                      names: names.slice(0, 3).join(t("team.list_separator")),
                      n: names.length - 3,
                    })
                  : names.join(t("team.list_separator")))}
            </span>
            <span className="teamreq-dials">
              {offer.length > 0 ? (
                <span className="teamreq-connectors">
                  <span className="teamreq-dial-label">
                    {t("team.connectors_for_all", { n })}
                  </span>
                  {offer.map(groupBox)}
                  {extra.length === 0 && addButton}
                </span>
              ) : extra.length === 0 && other.length === 0 ? (
                <span className="teamreq-connectors">
                  <span className="teamreq-dial-label">
                    {t("persona.connectors")}
                  </span>
                  <span className="teamreq-none">
                    {t("team.none_available")}
                  </span>
                </span>
              ) : null}
              {extra.length > 0 && (
                <span className="teamreq-connectors teamreq-beyond">
                  <span className="teamreq-dial-label">
                    {t("team.beyond_role")}
                  </span>
                  {extra.map(groupBox)}
                  {addButton}
                </span>
              )}
              {offer.length === 0 && extra.length === 0 && addButton}
              {groupAdding[persona] && addable.length > 0 && (
                <span className="teamreq-connectors">
                  {addable.map((c) => (
                    <label key={c} className="teamreq-connector">
                      <input
                        type="checkbox"
                        data-testid={`teamreq-group-connector-${persona}-${c}`}
                        checked={false}
                        onChange={() => setForWorkers(idxs, c, true)}
                      />
                      <span>{connectorLabel(c)}</span>
                    </label>
                  ))}
                </span>
              )}
            </span>
          </span>
        </div>
        {isOpen && (
          <div
            className="teamreq-group-members"
            data-testid={`teamreq-group-members-${persona}`}
          >
            {idxs.map(memberRow)}
          </div>
        )}
      </div>
    );
  };
  return (
    <div
      className="dirreq-card teamreq-card proposal-card"
      data-testid="teamreq-card"
    >
      <div className="proposal-content">
        <header className="proposal-heading">
          <span className="proposal-mark">
            <Icon name="team" size={20} />
          </span>
          <div>
            <span className="proposal-label">
              {t("proposal.proposed_team")}
            </span>
            <h3>
              {item.title || t("team.creating", { count: item.members.length })}
            </h3>
          </div>
        </header>
        {item.summary && <p className="proposal-summary">{item.summary}</p>}
        <div className="teamreq-head">
          <span className="teamreq-title">
            {/* Roles show only when some role has 2+ workers, so that sentence is always "N workers". */}
            {groups.length < item.members.length
              ? t("team.creating_roles", {
                  workers: item.members.length,
                  count: groups.length,
                })
              : t("team.creating", { count: item.members.length })}
          </span>
        </div>
        {note && (
          <div className="teamreq-note-wrap">
            <div
              className={
                "teamreq-note" + (noteLong && !noteOpen ? " clamped" : "")
              }
            >
              {note}
            </div>
            {noteLong && (
              <button
                className="teamreq-note-toggle"
                data-testid="teamreq-note-toggle"
                onClick={() => setNoteOpen((o) => !o)}
              >
                {noteOpen ? t("team.less") : t("team.more")}
              </button>
            )}
          </div>
        )}
        {/* The roster scrolls inside the card; the header above and the decision below stay
          in view however many workers the lead proposes. */}
        <div className="teamreq-roster" data-testid="teamreq-roster" ref={rosterRef}>
          {item.groups?.length
            ? item.groups.map((group) => {
                const idxs = item.members
                  .map((_, i) => i)
                  .filter((i) => item.members[i].group === group.id);
                return (
                  <details className="proposal-group" key={group.id}>
                    <summary>
                      <Icon name="chevronRight" size={14} />
                      <span className="proposal-group-title">
                        {group.title}
                        <small>{group.summary}</small>
                      </span>
                      <span className="proposal-muted">
                        {t("proposal.workers", { count: idxs.length })}
                      </span>
                    </summary>
                    <div className="proposal-group-content">
                      {idxs.map(memberRow)}
                    </div>
                  </details>
                );
              })
            : groups.map((g) =>
                g.idxs.length === 1 ? memberRow(g.idxs[0]) : groupRow(g),
              )}
        </div>
        {[
          ...new Map(
            (item.planned_items || []).flatMap((task) =>
              task.final_acceptance
                ? [[task.final_acceptance.id, task.final_acceptance] as const]
                : [],
            ),
          ).values(),
        ].map((final) => (
          <div className="proposal-final" key={final.id}>
            <span className="proposal-label">
              {t("proposal.final_acceptance")}
            </span>
            <p>{final.title}</p>
            <p className="proposal-muted">
              {t(
                final.owner === "lead"
                  ? "proposal.owner_lead"
                  : "proposal.owner_assigned_worker",
              )}
            </p>
          </div>
        ))}
        <div
          className="proposal-access"
          data-testid="proposal-connector-summary"
        >
          <div className="proposal-access-heading">
            <span className="proposal-label">{t("proposal.connector_access")}</span>
            <button
              className="teamreq-add"
              data-testid="proposal-manage-connectors"
              onClick={() => {
                rosterRef.current?.querySelectorAll("details").forEach((group) => { group.open = true; });
                setOpen(Object.fromEntries(groups.map((group) => [group.persona, true])));
                setAdding(Object.fromEntries(item.members.map((_, i) => [i, true])));
                rosterRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
              }}
            >
              {t("proposal.manage_connectors")}
            </button>
          </div>
          {[...new Set(Object.values(ticked).flat())].length ? (
            [...new Set(Object.values(ticked).flat())].map((connector) => (
              <span key={connector}>
                {connectorLabel(connector)} ·{" "}
                {t("proposal.workers", {
                  count: Object.values(ticked).filter((list) =>
                    list.includes(connector),
                  ).length,
                })}
              </span>
            ))
          ) : (
            <span>{t("proposal.no_connectors")}</span>
          )}
        </div>
        {mode && (
          <div className="teamreq-approvals" data-testid="teamreq-approvals">
            <Trans
              i18nKey="team.approvals_follow"
              values={{ mode: modeLabel(mode) }}
              components={{ b: <b /> }}
            />
          </div>
        )}
        <div className="teamreq-advanced">
          <button type="button" className="teamreq-add" aria-expanded={advanced} data-testid="teamreq-advanced"
            onClick={() => setAdvanced(value => !value)}>
            <Icon name={advanced ? "chevronDown" : "chevronRight"} size={12} /> {t("team.advanced")}
          </button>
          {advanced && <div className="teamreq-guidance">
            <p className="proposal-muted">{t("team.guidance_help")}</p>
            {item.members.map((m, i) => <label key={i}>
              <span>{t("team.approval_guidance")} · {names[i] || m.persona}</span>
              <textarea rows={3} maxLength={2400} value={guidance[i]}
                onChange={event => setGuidance(current => current.map((text, j) => j === i ? event.target.value : text))} />
            </label>)}
          </div>}
        </div>
        <label className="teamreq-chat">
          <input
            type="checkbox"
            data-testid="teamreq-chat-toggle"
            checked={chat}
            onChange={(e) => setChat(e.target.checked)}
          />
          <span>
            <Trans i18nKey="team.enable_chat" components={{ b: <b /> }} />
          </span>
          <span className="teamreq-info" title={t("team.chat_info")}>
            i
          </span>
        </label>
        <p className="proposal-muted proposal-permission-note">
          {t("proposal.staffing_not_assignment")}
        </p>
        {invalidNames && <p role="alert">{t("proposal.invalid_names")}</p>}
      </div>
      <ProposalActions
        approveLabel={t("team.create_team")}
        testId="teamreq-approve"
        disabled={invalidNames}
        onApprove={() => onRespond(true, undefined, chat, decisions())}
        onReject={(feedback) => onRespond(false, feedback)}
      />
    </div>
  );
}
