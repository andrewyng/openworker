import { esc } from "./observability-data.js";
import { evalEndpoint, jobHref } from "./eval-lists.js";
const path = evalEndpoint + "/schedules";
const time = (ms) => esc(new Date(ms).toLocaleString());
const status = {
  queued: "Enqueued", no_matches: "No matching turns", pending: "Preparing",
  credential_unavailable: "Credential expired or revoked", selection_unavailable: "Selection unavailable",
};

function history(schedule) {
  const dataset = schedule.spec.target.dataset_id;
  return schedule.runs.length ? `<ul>${schedule.runs.map((run) =>
    `<li>${time(run.end_ms)} · ${run.state === "queued"
      ? `<a href="${jobHref(dataset,run.job_id)}">Open run ↗</a>` : esc(status[run.state] || run.state)}
      ${run.clipped ? " · Case limit reached" : ""}</li>`).join("")}</ul>` : '<p class="ev-muted">No runs yet.</p>';
}

export function mountSchedules(root, { api,query,canRun,signal }) {
  root.innerHTML = `
    <nav class="ev-breadcrumb" aria-label="Evaluation breadcrumb"><a href="#evals">Evals</a><span aria-hidden="true">/</span><span>Schedules</span></nav>
    <header class="ev-heading"><div><h1>Schedules</h1><p>Run an evaluation on a recurring cadence.</p></div></header>
    ${canRun ? `<details class="ev-section ev-disclosure ev-run-selection" data-schedule-editor>
      <summary>New schedule</summary>
      <form class="ev-capture ev-evaluator-editor">
        <label>Name<input name="name" maxlength="100" required></label>
        <label>Source<select name="kind"><option value="tags">Tagged turns</option><option value="dataset">Dataset version</option></select></label>
        <div class="ev-capture" data-tag-fields>
          <label>Tag<input name="tag" maxlength="138" required></label>
          <label>Evaluator<select name="evaluator_id" aria-label="Evaluator" required></select></label>
          <label>Look back hours<input type="number" name="lookback_hours" value="24" min="1" max="168" required></label>
          <label>Maximum turns per run<input type="number" name="max_cases" value="20" min="1" max="100" required></label>
        </div>
        <div class="ev-capture" data-dataset-fields hidden>
          <label>Dataset<select name="dataset_id" disabled required></select></label>
          <label>Version<input name="version" type="number" min="1" disabled required></label>
        </div>
        <label>Run every (hours)<input type="number" name="every_hours" value="24" min="1" max="168" required></label>
        <p class="ev-error" role="alert"></p>
        <div><button class="button primary" type="submit">Save schedule</button></div>
      </form>
    </details>` : ""}
    <section class="ev-section" data-schedules aria-label="Saved schedules"><p class="ev-muted" role="status">Loading schedules…</p></section>`;
  const editor=root.querySelector("[data-schedule-editor]");
  const list=root.querySelector("[data-schedules]");
  const form=editor?.querySelector("form");
  const schedules=new Map();
  let previous, updating=false;
  const get=(p)=>api(p,"GET",undefined,undefined,signal);
  async function load() {
    try {
      const page=await get(path);
      if (signal.aborted) return;
      schedules.clear();
      for (const item of page.schedules) schedules.set(item.id,item);
      list.innerHTML=page.schedules.length ? `<div class="ev-list">${page.schedules.map((schedule) => {
        const { spec,enabled,id }=schedule;
        const target=spec.target.type==="tags" ? spec.target.tag : "Dataset v"+spec.target.version;
        return `<details class="ev-case"><summary><span>${esc(spec.name)}<small class="ev-table-subtitle">${esc(target)} · Every ${spec.every_hours} h</small></span>
          <span class="ev-pill ev-${enabled?"passed":"queued"}"><i></i>${enabled?"Active":"Paused"}</span></summary>
          <div class="ev-case-detail"><p>${enabled?"Next run · "+time(schedule.next_ms):"Schedule paused"}</p>
            ${spec.target.type==="tags" ? `<p>Look back ${spec.target.lookback_hours} h · Up to ${spec.target.max_cases} turns</p>` : ""}
            <h3>Recent runs</h3>${history(schedule)}
            ${canRun ? `<button class="button" type="button" data-toggle="${esc(id)}">${enabled?"Pause":"Resume"}</button>`:""}
            <p class="ev-error" role="alert"></p>
          </div></details>`;
      }).join("")}</div>` : '<div class="ev-empty"><h3>No schedules yet</h3></div>';
    } catch {
      if (!signal.aborted) list.innerHTML='<p class="ev-error" role="alert">Couldn’t load schedules.</p>';
    }
  }
  list.addEventListener("click",async (event)=>{
    const button=event.target.closest("[data-toggle]");
    if (!button || updating) return;
    const schedule=schedules.get(button.dataset.toggle);
    updating=true;
    button.disabled=true;
    try {
      await api(path+"/"+encodeURIComponent(schedule.id),"POST",{enabled:!schedule.enabled},crypto.randomUUID(),signal);
      await load();
    } catch {
      if (!signal.aborted) button.parentElement.querySelector("[role=alert]").textContent="Couldn’t change this schedule.";
    } finally { updating=false;button.disabled=false; }
  },{signal});
  load();
  if (!form) return;
  const elements=form.elements;
  const switchSource=()=>{
    for (const [selector,visible] of [["[data-tag-fields]",elements.kind.value==="tags"],["[data-dataset-fields]",elements.kind.value==="dataset"]]) {
      const panel=form.querySelector(selector);
      panel.hidden=!visible;
      panel.querySelectorAll("input,select").forEach((field)=>field.disabled=!visible);
    }
  };
  elements.kind.addEventListener("change",switchSource,{signal});
  elements.dataset_id.addEventListener("change",()=>{
    const version=elements.dataset_id.selectedOptions[0]?.dataset.version;
    if (version) elements.version.value=version;
  },{signal});
  const requestedDataset=query.get("dataset");
  const requestedEvaluator=query.get("evaluator");
  if (requestedDataset) { elements.kind.value="dataset";elements.version.value=query.get("version");editor.open=true; }
  if (query.get("tag")) { elements.tag.value=query.get("tag");editor.open=true; }
  switchSource();
  const button=form.querySelector("button");
  button.disabled=true;
  Promise.all([get(evalEndpoint+"/datasets?limit=50"),get(evalEndpoint+"/evaluators")]).then(([data,rules])=>{
    if (signal.aborted) return;
    for (const dataset of data.datasets) {
      const option=new Option(dataset.name,dataset.id);option.dataset.version=dataset.version;elements.dataset_id.add(option);
    }
    if (requestedDataset) {
      if (![...elements.dataset_id.options].some((option)=>option.value===requestedDataset)) elements.dataset_id.add(new Option("Selected dataset",requestedDataset));
      elements.dataset_id.value=requestedDataset;
    } else if (data.datasets[0]) { elements.version.value=data.datasets[0].version; }
    for (const rule of rules.evaluators) elements.evaluator_id.add(new Option(rule.name+" · v"+rule.version,rule.id));
    if (requestedEvaluator) {
      if (![...elements.evaluator_id.options].some((option)=>option.value===requestedEvaluator)) elements.evaluator_id.add(new Option("Selected evaluator",requestedEvaluator));
      elements.evaluator_id.value=requestedEvaluator;
    }
    button.disabled=false;
  }).catch(()=>{ if (!signal.aborted) form.querySelector("[role=alert]").textContent="Couldn’t load datasets and evaluators."; });
  form.addEventListener("submit",async(event)=>{
    event.preventDefault();
    const values=new FormData(form);
    const type=values.get("kind");
    const target=type==="dataset"?{type,dataset_id:values.get("dataset_id"),version:Number(values.get("version"))}
      : {type,tag:values.get("tag").trim(),evaluator_id:values.get("evaluator_id"),
        lookback_hours:Number(values.get("lookback_hours")),max_cases:Number(values.get("max_cases"))};
    const body={name:values.get("name").trim(),every_hours:Number(values.get("every_hours")),target};
    const snapshot=JSON.stringify(body);
    if (previous?.snapshot!==snapshot) previous={snapshot,key:crypto.randomUUID()};
    button.disabled=true;
    try {
      await api(path,"POST",body,previous.key,signal);
      if (signal.aborted) return;
      previous=null;
      editor.open=false;
      elements.name.value="";
      await load();
    } catch {
      if (!signal.aborted) form.querySelector("[role=alert]").textContent="Couldn’t save this schedule. Check the source and limits.";
    } finally { button.disabled=false; }
  },{signal});
}
