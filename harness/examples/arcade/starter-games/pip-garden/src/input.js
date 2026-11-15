// Keyboard + touch input. Held state is cleared on blur/visibility loss.
const KEYMAP = {
  KeyW: 'up', ArrowUp: 'up', KeyS: 'down', ArrowDown: 'down',
  KeyA: 'left', ArrowLeft: 'left', KeyD: 'right', ArrowRight: 'right', Space: 'jump'
};
export function createInput({ onPauseKey }) {
  const held = { up: false, down: false, left: false, right: false, jump: false };
  let jumpQueued = false;
  const touchBtns = [...document.querySelectorAll('[data-k]')];
  addEventListener('keydown', e => {
    if (e.code === 'KeyP' || e.code === 'Escape') { onPauseKey(); e.preventDefault(); return; }
    const k = KEYMAP[e.code]; if (!k) return;
    e.preventDefault();
    if (k === 'jump' && !held.jump && !e.repeat) jumpQueued = true;
    held[k] = true;
  });
  addEventListener('keyup', e => { const k = KEYMAP[e.code]; if (k) held[k] = false; });
  for (const b of touchBtns) {
    const k = b.dataset.k;
    const down = e => { e.preventDefault(); b.setPointerCapture?.(e.pointerId); if (k === 'jump') jumpQueued = true; held[k] = true; b.classList.add('on'); };
    const up = () => { held[k] = false; b.classList.remove('on'); };
    b.addEventListener('pointerdown', down);
    for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) b.addEventListener(ev, up);
    b.addEventListener('contextmenu', e => e.preventDefault());
  }
  return {
    held,
    // Movement vector per brief: up = -Z, down = +Z, left = -X, right = +X.
    axis() {
      let x = (held.right ? 1 : 0) - (held.left ? 1 : 0), z = (held.down ? 1 : 0) - (held.up ? 1 : 0);
      const l = Math.hypot(x, z); if (l > 0) { x /= l; z /= l; }
      return { x, z };
    },
    takeJump() { const j = jumpQueued; jumpQueued = false; return j; },
    clear() {
      for (const k in held) held[k] = false;
      jumpQueued = false;
      for (const b of touchBtns) b.classList.remove('on');
    }
  };
}
