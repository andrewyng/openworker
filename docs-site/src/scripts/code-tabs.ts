// One language selection follows the reader through all examples on the page.
function initializeKind(kind: 'language' | 'installer') {
  const storageKey = `openworker-docs-code-${kind}`;
  const groups = [...document.querySelectorAll<HTMLElement>(`[data-sdk-tabs][data-tab-kind="${kind}"]`)];
  const options = kind === 'language' ? ['typescript', 'rust'] : ['codex', 'claude'];
  let initial = options[0]!;
  try { initial = sessionStorage.getItem(storageKey) ?? initial; } catch { /* storage is optional */ }
  function select(language: string) {
    for (const group of groups) {
      const buttons = [...group.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
      if (!buttons.some(button => button.dataset.language === language)) continue;
      for (const button of buttons) {
        const active = button.dataset.language === language;
        button.setAttribute('aria-selected', String(active));
        button.tabIndex = active ? 0 : -1;
      }
      for (const panel of group.querySelectorAll<HTMLElement>('[role="tabpanel"]'))
        panel.hidden = panel.dataset.languagePanel !== language;
    }
    try { sessionStorage.setItem(storageKey, language); } catch { /* keep page-local choice */ }
  }
  for (const group of groups) {
    if (group.dataset.ready) continue;
    group.dataset.ready = 'true';
    group.querySelector<HTMLElement>('[role="tablist"]')!.hidden = false;
    const buttons = [...group.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    for (const [index, button] of buttons.entries()) {
      button.addEventListener('click', () => select(button.dataset.language!));
      button.addEventListener('keydown', event => {
        let next: number;
        if (event.key === 'ArrowRight') next = (index + 1) % buttons.length;
        else if (event.key === 'ArrowLeft') next = (index + buttons.length - 1) % buttons.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = buttons.length - 1;
        else return;
        event.preventDefault();
        select(buttons[next]!.dataset.language!);
        buttons[next]!.focus();
      });
    }
  }
  select(options.includes(initial) ? initial : options[0]!);
}
function initializeTabs() { initializeKind('language'); initializeKind('installer'); }
initializeTabs();
document.addEventListener('astro:page-load', initializeTabs);
