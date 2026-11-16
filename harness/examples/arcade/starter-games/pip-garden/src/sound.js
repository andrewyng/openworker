// Tiny synthesized sounds. Audio starts only after a user gesture; failures are silent.
let ctx = null;
function ac() {
  try {
    if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  } catch { return null; }
}
addEventListener('pointerdown', ac, { once: true });
addEventListener('keydown', ac, { once: true });
function tone(freq, at, dur, vol = 0.12) {
  const c = ctx; if (!c || c.state !== 'running') return;
  const o = c.createOscillator(), g = c.createGain(), t = c.currentTime + at;
  o.type = 'triangle'; o.frequency.value = freq;
  g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol, t + 0.02);
  g.gain.exponentialRampToValueAtTime(0.001, t + dur);
  o.connect(g).connect(c.destination); o.start(t); o.stop(t + dur + 0.05);
}
const SCALE = [659, 784, 880, 988, 1175];
export function chime(n) { ac(); const f = SCALE[(n - 1) % SCALE.length]; tone(f, 0, 0.35); tone(f * 1.5, 0.06, 0.3, 0.06); }
export function fanfare() { ac(); [523, 659, 784, 1047].forEach((f, i) => tone(f, i * 0.12, 0.5, 0.1)); }
