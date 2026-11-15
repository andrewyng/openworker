// HTML comments are invisible on GitHub; the headings/fences remain normal Markdown.
const LABELS = new Map([
  ['TypeScript', 'typescript'], ['Rust', 'rust'],
  ['TypeScript / Electron', 'typescript'], ['Rust / Tauri', 'rust'],
]);
const INSTALL_LABELS = new Map([['Codex', 'codex'], ['Claude Code', 'claude']]);
export function renderCodeTabs(markdown, page) {
  let group = 0;
  const output = markdown.replace(/^<!-- sdk-(tabs|install-tabs) -->\s*\n([\s\S]*?)^<!-- \/sdk-\1 -->/gm, (_, kind, body) => {
    const install = kind === 'install-tabs';
    const labels = install ? INSTALL_LABELS : LABELS;
    const parts = [...body.matchAll(/^### (.+)\n([\s\S]*?)(?=^### |$(?![\s\S]))/gm)];
    if (parts.length !== 2 || parts.some(p => !labels.has(p[1].trim())))
      throw Error(`Expected ${install ? 'Codex and Claude Code' : 'TypeScript and Rust'} in SDK tabs: ${page}`);
    const keys = parts.map(p => labels.get(p[1].trim()));
    if (new Set(keys).size !== 2 || !body.trimStart().startsWith('### '))
      throw Error(`Duplicate or malformed SDK tabs: ${page}`);
    const id = `${page}-code-${++group}`;
    const buttons = parts.map((p, i) => `<button type="button" role="tab" id="${id}-${keys[i]}-tab" aria-controls="${id}-${keys[i]}-panel" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}" data-language="${keys[i]}">${p[1].trim()}</button>`).join('\n');
    const panels = parts.map((p, i) => `<div role="tabpanel" id="${id}-${keys[i]}-panel" aria-labelledby="${id}-${keys[i]}-tab" data-language-panel="${keys[i]}" tabindex="0">\n\n<h4 class="sdk-tab-fallback">${p[1].trim()}</h4>\n\n${p[2].trim()}\n\n</div>`).join('\n\n');
    // Without JavaScript, both labelled examples remain readable.
    return `<div class="sdk-code-tabs${install ? ' sdk-install-tabs' : ''}" data-sdk-tabs data-tab-kind="${install ? 'installer' : 'language'}">\n<div role="tablist" aria-label="${install ? 'Coding agent' : 'Example language'}" hidden>\n${buttons}\n</div>\n\n${panels}\n\n</div>`;
  });
  if (/^<!-- \/?sdk-(?:tabs|install-tabs) -->/m.test(output)) throw Error(`Unclosed SDK tabs: ${page}`);
  return output.replace(/^```rust,(?:ignore|no_run)\s*$/gm, '```rust');
}
