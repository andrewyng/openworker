// Pure presentation helpers. Provider-reported values stay optional; they are
// neither a bill nor inferred per-tool token costs.
export const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const known = (n) =>
  typeof n === "number" &&
  Number.isFinite(n) &&
  n >= 0 &&
  n <= Number.MAX_SAFE_INTEGER;
export const count = (n) =>
  known(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—";
export function duration(us) {
  if (!known(us)) return "—";
  if (us === 0) return "0 ms";
  if (us < 1000) return "<1 ms";
  if (us < 1000000)
    return `${(us / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} ms`;
  if (us < 60000000)
    return `${(us / 1000000).toLocaleString("en-US", { maximumFractionDigits: 1 })} s`;
  return `${(us / 60000000).toLocaleString("en-US", { maximumFractionDigits: 1 })} min`;
}
export const measurement = (m, formatter = count) =>
  `${formatter(m?.total)}${m?.unknown > 0 && known(m.total) ? " + unknown" : ""}`;
export const average = (m) =>
  m?.known > 0 && known(m.total) ? m.total / m.known : null;
export const runKey = (r) => `${r.session_id}/${r.turn_id}`;
export const purpose = (p) =>
  ({
    main: "Main agent",
    reviewer: "Auto-approver",
    auto_approver: "Auto-approver",
    compaction: "Compaction",
    summary: "Compaction",
    helper: "Helper",
    title: "Title",
  })[p] ||
  p ||
  "Unspecified";
export const phases = [
  ["model", "Model requests"],
  ["tool", "Tool execution"],
  ["policy", "Policy checks"],
  ["approval_wait", "Waiting for approval"],
  ["question_wait", "Waiting for answer"],
  ["other_active", "Other activity"],
  ["parallel", "Parallel work"],
  ["unobserved_gap", "Unobserved gaps"],
];
export function usageTotal(u) {
  // A combined token number is only useful if both counters were reported.
  if (!known(u?.input?.total) || !known(u?.output?.total)) return null;
  const total = u.input.total + u.output.total;
  return Number.isSafeInteger(total) ? total : null;
}
export function purposeUsage(models = []) {
  const groups = new Map();
  for (const m of models) {
    const name = purpose(m.purpose),
      current = groups.get(name) || { name, total: 0, missing: 0, known: 0 };
    const total = usageTotal(m.usage);
    if (known(total)) {
      current.total += total;
      current.known++;
    }
    if (!known(total) || m.usage?.input?.unknown || m.usage?.output?.unknown)
      current.missing++;
    groups.set(name, current);
  }
  return [...groups.values()].map((g) => ({
    ...g,
    total: g.known && Number.isSafeInteger(g.total) ? g.total : null,
  }));
}
export function coverageNotes(c = {}) {
  const notes = [];
  if (!c.scanned_steps) notes.push("No recorded steps for this selection.");
  if (c.scan_truncated) notes.push("The 4,096-step summary limit was reached.");
  if (c.group_truncated)
    notes.push(
      "Only the first 128 model/tool groups are listed; group sums may differ from totals.",
    );
  if (known(c.expected_segments) && c.recorded_segments !== c.expected_segments)
    notes.push(
      `${count(c.recorded_segments)} of ${count(c.expected_segments)} execution segments recorded.`,
    );
  if (c.scanned_steps && !c.all_visible_segments_closed)
    notes.push("Visible execution segments are unfinished or missing.");
  if (c.pending_waits > 0)
    notes.push(`${count(c.pending_waits)} pending human response(s).`);
  if (c.requests_without_completion > 0)
    notes.push(
      `${count(c.requests_without_completion)} model request(s) without a completion record.`,
    );
  if (c.completions_without_start > 0)
    notes.push(
      `${count(c.completions_without_start)} model completion(s) without a start record.`,
    );
  if (c.unknown_timing_steps > 0)
    notes.push(
      `${count(c.unknown_timing_steps)} step(s) without measured duration.`,
    );
  if (c.unknown_outcome_steps > 0)
    notes.push(
      `${count(c.unknown_outcome_steps)} step(s) with unknown outcome.`,
    );
  if (c.clock_or_range_anomaly)
    notes.push("Clock or timing range anomalies were detected.");
  if (c.hierarchy_incomplete)
    notes.push(
      "Parent relationships are missing or cyclic; phase attribution may be incomplete.",
    );
  return notes;
}
export function timelineLayout(records) {
  const steps = records
    .filter((r) => known(r.step.started_ms))
    .slice()
    .sort(
      (a, b) => a.step.started_ms - b.step.started_ms || a.cursor - b.cursor,
    );
  const starts = steps.map((r) => r.step.started_ms * 1000);
  const from = starts.length ? Math.min(...starts) : 0;
  const to = steps.length
    ? Math.max(
        ...steps.map(
          (r) =>
            r.step.started_ms * 1000 +
            (known(r.step.duration_us) ? r.step.duration_us : 0),
        ),
      )
    : from;
  const span = Math.max(1, to - from);
  return {
    from,
    to,
    rows: steps.map((r) => ({
      ...r,
      left: Math.max(
        0,
        Math.min(100, ((r.step.started_ms * 1000 - from) / span) * 100),
      ),
      width: known(r.step.duration_us)
        ? Math.max(0, Math.min(100, (r.step.duration_us / span) * 100))
        : 0,
    })),
  };
}
