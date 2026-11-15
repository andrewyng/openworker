const esc = value => String(value ?? "—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
export function renderCompliance(state) {
  if (!state) return '<p class="small-note">Loading safety activity…</p>';
  if (state.error) return `<p class="small-note" role="status">${esc(state.error)}</p>`;
  const findings=state.activities.filter(e=>e.kind==="safety_evaluation");
  const counts={blocked:0,redacted:0,unavailable:0};
  for(const e of findings)if(e.facts?.outcome in counts)counts[e.facts.outcome]++;
  const health=state.sinks.length?`<details class="export-note"><summary>Export delivery</summary>${state.sinks.map(s=>`<div class="setting-row"><span>${esc(s.sink)}</span><span>${esc(s.pending)} pending${s.failures?` · retry scheduled`:""}</span></div>`).join("")}</details>`:"";
  return `<div class="safety-summary">${Object.entries(counts).map(([label,n])=>`<div><strong>${n}</strong><span>${esc(label)}</span></div>`).join("")}</div>
    <p class="small-note">${state.activities.length} loaded records · metadata only</p>
    ${findings.length?`<div class="timeline">${findings.map(e=>`<details class="safety-finding"><summary><span>${esc(e.facts?.check)}</span><span class="tag">${esc(e.facts?.outcome)}</span></summary><div class="small-note">${esc(e.facts?.phase)} · ${esc(e.facts?.durationMs??0)} ms</div><pre>${esc(JSON.stringify({id:e.id,turnId:e.turnId,...e.facts},null,2))}</pre></details>`).join("")}</div>`:'<div class="empty">No safety decisions in these records.</div>'}
    ${state.hasMore?'<button class="button" data-action="more-safety">Load more</button>':""}${health}`;
}
