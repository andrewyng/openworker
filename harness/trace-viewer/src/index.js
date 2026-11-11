export {mountSessionTracing, serviceTracingAdapter} from './tracing.js';
import {mountObservability} from './observability.js';
import {count, measurement} from './observability-data.js';
export {mountObservability};

/** Mount one turn. The trusted application owns authorization and transport. */
export function mountTurnTrace(root, {adapter, sessionId, turnId, onChild}) {
  root.classList.add('ow-trace-viewer');
  const query = {sessionId, turnId};
  const request = async path => {
    const url = new URL(path, 'https://viewer.invalid');
    const match = /^\/v1\/sessions\/([^/]+)\/observability\/([^/]+)\/(timeline|summary)$/.exec(url.pathname);
    if (!match || decodeURIComponent(match[1]) !== sessionId || decodeURIComponent(match[2]) !== turnId)
      throw Error('Unsupported trace scope');
    const q = {...query};
    for (const key of ['after', 'through', 'limit'])
      if (url.searchParams.has(key)) q[key] = Number(url.searchParams.get(key));
    return adapter[match[3]](q);
  };
  const unmount = mountObservability(root, {request, initialRun: {session_id: sessionId, turn_id: turnId}, directory: false, openSession: false});
  const extra = document.createElement('div'); root.append(extra);
  let disposed = false, generation = 0;
  const paragraph = text => { const p = document.createElement('p'); p.textContent = text; return p; };
  const section = title => {
    const node = document.createElement('section'); node.className = 'obs-panel';
    const h = document.createElement('h2'); h.textContent = title; node.append(h); return node;
  };
  async function details() {
    const mine = ++generation;
    extra.replaceChildren();
    const tasks = [];
    if (adapter.children) tasks.push(['Sub-agents', async () => {
      const page = await adapter.children(query), node = section('Sub-agents');
      for (const child of page.children) {
        if (onChild && child.child_turn_id) {
          const b = document.createElement('button'); b.className = 'button';
          b.textContent = `${child.agent_id} · ${child.phase}`;
          b.addEventListener('click', () => onChild(child)); node.append(b);
        } else node.append(paragraph(`${child.agent_id} · ${child.phase}`));
      }
      if (!page.children.length) node.append(paragraph('No visible recorded child links for this turn.'));
      return node;
    }]);
    if (adapter.subtreeSummary) tasks.push(['Linked work', async () => {
      const tree = await adapter.subtreeSummary(query), node = section('Linked work');
      node.append(paragraph(`${count(tree.selection.runs.length)} visible initial tasks${tree.selection.selection_truncated ? ' · selection limit reached' : ''}`));
      node.append(paragraph(`Input: ${measurement(tree.summary.usage.input)} · Output: ${measurement(tree.summary.usage.output)} tokens`));
      node.append(paragraph('Includes this turn and spawned initial tasks. Later messages are separate turns; concurrent durations are not elapsed time.'));
      return node;
    }]);
    if (adapter.evaluations) tasks.push(['Evaluations', async () => {
      const page = await adapter.evaluations({...query, limit: 25}), node = section('Evaluations');
      for (const row of page.results) {
        const e = row.evaluation.result;
        const details = document.createElement('details'); details.className = 'obs-details';
        const summary = document.createElement('summary');
        summary.textContent = `${e.name} · ${e.status === 'completed' ? String(e.score?.value) : e.status}`;
        details.append(summary, paragraph(`${e.evaluator.name} · ${e.evaluator.version} · ${e.evaluator.source}`));
        if (e.explanation) details.append(paragraph(e.explanation));
        if (e.supersedes) details.append(paragraph(`Corrects ${e.supersedes}; original evidence retained.`));
        node.append(details);
      }
      if (!page.results.length) node.append(paragraph('No recorded evaluations.'));
      if (page.next !== null) node.append(paragraph('Showing the first 25 evaluations. Use the query API to read later pages.'));
      return node;
    }]);
    const results = await Promise.all(tasks.map(async ([title, task]) => {
      try {return await task();} catch {const node = section(title); node.append(paragraph('Unavailable or not enabled for this application.')); return node;}
    }));
    if (!disposed && mine === generation) extra.replaceChildren(...results);
  }
  const refresh = () => {root.dispatchEvent(new Event('ow-trace-refresh')); details();};
  const click = e => {if (e.target.closest('[data-obs="refresh"]')) details();};
  root.addEventListener('click', click); details();
  return {refresh, dispose() {disposed = true; generation++; root.removeEventListener('click', click); unmount(); root.replaceChildren(); root.classList.remove('ow-trace-viewer');}};
}
