(() => {
  'use strict';
  const canvas = document.querySelector('#arena');
  const ctx = canvas.getContext('2d');
  const $ = id => document.getElementById(id);
  const W = 1000, H = 650, GOAL = 18;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const keys = new Set();
  let state = 'title', last = 0, elapsed = 0, timer = 60, energy = 0, lives = 3;
  let player, enemies = [], shards = [], sparks = [], trail = [], dashTime = 0, cooldown = 0, invincible = 0;
  let view = { scale: 1, x: 0, y: 0 }, pointer = null, toastTime = 0;
  let visualTime = 0;
  const rand = (a, b) => a + Math.random() * (b - a);
  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  function resize() {
    const rect = canvas.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * dpr); canvas.height = Math.round(rect.height * dpr);
    view.scale = Math.min(rect.width / W, rect.height / H);
    view.x = (rect.width - W * view.scale) / 2; view.y = (rect.height - H * view.scale) / 2;
    view.dpr = dpr;
  }
  new ResizeObserver(resize).observe(canvas);
  function makeShard() {
    let s;
    for (let i = 0; i < 100; i++) {
      s = { x: rand(65, W - 65), y: rand(75, H - 75), phase: rand(0, Math.PI * 2) };
      if (distance(s, player) > 110 && shards.every(o => distance(s, o) > 85)) break;
    }
    return s;
  }
  function reset() {
    player = { x: W / 2, y: H / 2, vx: 0, vy: 0, angle: -Math.PI / 2 };
    enemies = Array.from({ length: 5 }, (_, i) => {
      const a = i * Math.PI * 2 / 5;
      return { x: W / 2 + Math.cos(a) * 360, y: H / 2 + Math.sin(a) * 210, vx: Math.sin(a) * 80, vy: -Math.cos(a) * 80, phase: a };
    });
    shards = []; for (let i = 0; i < 6; i++) shards.push(makeShard());
    sparks = []; trail = []; timer = 60; elapsed = energy = dashTime = cooldown = invincible = 0; lives = 3; pointer = null; keys.clear();
    updateHUD();
  }
  function updateHUD() {
    $('energy').innerHTML = String(energy).padStart(2, '0') + '<span> / 18</span>';
    $('clock').innerHTML = Math.ceil(timer) + '<span>s</span>';
    $('clock').style.color = timer <= 10 ? '#ff708e' : '';
    $('lives').textContent = '◆ '.repeat(lives) + '◇ '.repeat(3 - lives);
    $('lives').setAttribute('aria-label', lives + ' lives');
    $('dash-fill').style.width = Math.max(0, 1 - cooldown / 2.8) * 100 + '%';
    $('dash').disabled = state !== 'playing' || cooldown > 0;
  }
  function begin() {
    if (state === 'paused') { state = 'playing'; $('overlay').hidden = true; $('pause').textContent = 'Ⅱ'; $('pause').setAttribute('aria-label', 'Pause game'); }
    else { reset(); state = 'playing'; $('overlay').hidden = true; $('pause').disabled = false; $('hint').textContent = 'GOLD IS ENERGY · PINK IS TROUBLE'; toast('COLLECT 18 SHARDS. MAKE IT HOME.'); }
    updateHUD(); canvas.focus();
  }
  function toast(message) { $('toast').textContent = message; toastTime = 2.4; }
  function showCard(label, title, message, action) {
    $('eyebrow').textContent = label; $('heading').innerHTML = title; $('message').innerHTML = message;
    $('start').innerHTML = action + ' <span>↗</span>'; $('instructions').hidden = true; $('overlay').hidden = false; $('start').focus();
  }
  function finish(won) {
    state = won ? 'won' : 'lost'; pointer = null; keys.clear(); $('pause').disabled = true;
    showCard(won ? 'ALL ENERGY SECURED' : 'SIGNAL LOST', won ? 'CIRCUIT<br><span>CLEAR</span>' : 'END OF<br><span>THE LINE</span>', won ? `Beautiful driving. <b>${energy} shards</b> collected<br>with <b>${Math.ceil(timer)} seconds</b> to spare.` : `${lives ? 'The city clock ran out.' : 'The sentinels caught up.'}<br>You recovered <b>${energy} of ${GOAL} shards</b>. Find a new line.`, 'RUN IT BACK');
    updateHUD();
  }
  function togglePause() {
    if (state === 'playing') {
      $('toast').textContent = '';
      state = 'paused'; pointer = null; keys.clear(); $('pause').textContent = '▶'; $('pause').setAttribute('aria-label', 'Resume game');
      showCard('TAKE A BREATHER', 'IN THE<br><span>QUIET</span>', 'Your circuit is waiting.<br>Pick up right where you left off.', 'BACK TO THE LIGHT'); updateHUD();
    } else if (state === 'paused') begin();
  }
  function dash() {
    if (state !== 'playing' || cooldown > 0) return;
    dashTime = .24; cooldown = 2.8; invincible = Math.max(invincible, .38); toast('DASH — SENTINELS CAN’T TOUCH YOU');
    burst(player.x, player.y, '#46f8d0', 12);
  }
  function burst(x, y, color, count) {
    if (reduced) count = Math.min(count, 5);
    for (let i = 0; i < count; i++) { const a = rand(0, Math.PI * 2), speed = rand(35, 180); sparks.push({ x, y, vx: Math.cos(a) * speed, vy: Math.sin(a) * speed, life: rand(.25, .65), color }); }
  }
  function update(dt) {
    elapsed += dt; timer = Math.max(0, timer - dt); cooldown = Math.max(0, cooldown - dt); invincible = Math.max(0, invincible - dt); dashTime = Math.max(0, dashTime - dt);
    let dx = (keys.has('arrowright') || keys.has('d') ? 1 : 0) - (keys.has('arrowleft') || keys.has('a') ? 1 : 0);
    let dy = (keys.has('arrowdown') || keys.has('s') ? 1 : 0) - (keys.has('arrowup') || keys.has('w') ? 1 : 0);
    let amount = 1;
    if (pointer) { dx = pointer.x - player.x; dy = pointer.y - player.y; amount = Math.min(1, Math.hypot(dx, dy) / 65); }
    const length = Math.hypot(dx, dy);
    if (length > 0) { dx /= length; dy /= length; player.angle = Math.atan2(dy, dx); }
    if (dashTime > 0 && !length) { dx = Math.cos(player.angle); dy = Math.sin(player.angle); }
    const speed = dashTime > 0 ? 720 : 265, acceleration = Math.min(1, dt * 9);
    player.vx += (dx * speed * amount - player.vx) * acceleration; player.vy += (dy * speed * amount - player.vy) * acceleration;
    player.x = Math.max(29, Math.min(W - 29, player.x + player.vx * dt)); player.y = Math.max(29, Math.min(H - 29, player.y + player.vy * dt));
    if (!reduced) { trail.push({ x: player.x, y: player.y, life: dashTime > 0 ? .55 : .32 }); if (trail.length > 70) trail.shift(); }
    trail.forEach(t => t.life -= dt); trail = trail.filter(t => t.life > 0);
    for (let i = shards.length - 1; i >= 0; i--) if (distance(player, shards[i]) < 25) { burst(shards[i].x, shards[i].y, '#eff799', 15); shards.splice(i, 1); energy++; shards.push(makeShard()); toast(energy % 6 === 0 ? `${energy} / 18 — KEEP THE CURRENT FLOWING` : '+1 ENERGY'); }
    for (const e of enemies) {
      const a = Math.atan2(player.y - e.y, player.x - e.x) + Math.sin(elapsed * .7 + e.phase) * .5;
      const speed = 80 + energy * 2.1;
      e.vx += (Math.cos(a) * speed - e.vx) * dt * .8; e.vy += (Math.sin(a) * speed - e.vy) * dt * .8;
      e.x += e.vx * dt; e.y += e.vy * dt;
      e.x = Math.max(25, Math.min(W - 25, e.x)); e.y = Math.max(25, Math.min(H - 25, e.y));
      if (!invincible && distance(player, e) < 26) {
        lives--; invincible = 1.8; burst(player.x, player.y, '#ff5b9a', 24); toast('SIGNAL DAMAGED — KEEP MOVING');
        const away = Math.atan2(e.y - player.y, e.x - player.x); e.x += Math.cos(away) * 90; e.y += Math.sin(away) * 90;
      }
    }
    for (const p of sparks) { p.x += p.vx * dt; p.y += p.vy * dt; p.life -= dt; } sparks = sparks.filter(p => p.life > 0);
    toastTime -= dt; if (toastTime <= 0) $('toast').textContent = '';
    updateHUD();
    if (lives <= 0) finish(false); else if (energy >= GOAL) finish(true); else if (timer <= 0) finish(false);
  }
  function line(x1, y1, x2, y2, color, width = 1) { ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }
  function diamond(x, y, r, color) { ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(x, y - r); ctx.lineTo(x + r * .65, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r * .65, y); ctx.closePath(); ctx.fill(); }
  function draw() {
    const dpr = view.dpr || 1; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#080e19'; ctx.fillRect(0, 0, canvas.width / dpr, canvas.height / dpr);
    ctx.translate(view.x, view.y); ctx.scale(view.scale, view.scale);
    const glow = ctx.createRadialGradient(500, 325, 10, 500, 325, 610); glow.addColorStop(0, '#102935'); glow.addColorStop(1, '#09101e'); ctx.fillStyle = glow; ctx.fillRect(0, 0, W, H);
    ctx.save(); ctx.beginPath(); ctx.rect(0, 0, W, H); ctx.clip();
    for (let x = 0; x <= W; x += 40) line(x, 0, x, H, '#87d5e109');
    for (let y = 0; y <= H; y += 40) line(0, y, W, y, '#87d5e109');
    ctx.strokeStyle = '#46f8d010'; ctx.lineWidth = 1;
    for (let i = 0; i < 3; i++) { ctx.beginPath(); ctx.ellipse(500, 325, 270 + i * 90, 140 + i * 70, -.12, 0, Math.PI * 2); ctx.stroke(); }
    ctx.strokeStyle = '#46f8d030'; ctx.setLineDash([7, 13]); ctx.strokeRect(20, 20, W - 40, H - 40); ctx.setLineDash([]);
    for (const [x, y, sx, sy] of [[20,20,1,1],[980,20,-1,1],[20,630,1,-1],[980,630,-1,-1]]) { line(x, y, x + sx * 35, y, '#46f8d080', 2); line(x, y, x, y + sy * 35, '#46f8d080', 2); }
    ctx.fillStyle = '#57939210'; ctx.font = '900 italic 110px system-ui'; ctx.textAlign = 'center'; ctx.fillText('SECTOR 07', 500, 365);
    for (const s of shards) {
      const pulse = reduced ? 1 : 1 + Math.sin(visualTime * 3 + s.phase) * .15;
      ctx.strokeStyle = '#dce99235'; ctx.beginPath(); ctx.arc(s.x, s.y, 20 * pulse, 0, Math.PI * 2); ctx.stroke();
      ctx.shadowColor = '#eaff96'; ctx.shadowBlur = 18; diamond(s.x, s.y, 9 * pulse, '#edffa3'); ctx.shadowBlur = 0;
      line(s.x - 4, s.y, s.x + 4, s.y, '#fffbd1');
    }
    if (trail.length > 1) { for (let i = 1; i < trail.length; i++) { const t = trail[i]; line(trail[i - 1].x, trail[i - 1].y, t.x, t.y, `rgba(70,248,208,${Math.min(.6, t.life)})`, 5 + t.life * 8); } }
    for (const e of enemies) {
      ctx.save(); ctx.translate(e.x, e.y); ctx.rotate(Math.atan2(e.vy, e.vx)); ctx.shadowColor = '#ff4e95'; ctx.shadowBlur = 17;
      ctx.fillStyle = '#ff4e95'; ctx.beginPath(); ctx.moveTo(16, 0); ctx.lineTo(-10, -12); ctx.lineTo(-5, 0); ctx.lineTo(-10, 12); ctx.closePath(); ctx.fill(); ctx.shadowBlur = 0;
      line(-19, -7, -32, -7, '#ff4e9544', 2); line(-19, 7, -32, 7, '#ff4e9544', 2); ctx.restore();
    }
    for (const p of sparks) { ctx.globalAlpha = Math.min(1, p.life * 2); ctx.fillStyle = p.color; ctx.fillRect(p.x - 2, p.y - 2, 4, 4); } ctx.globalAlpha = 1;
    ctx.save(); ctx.translate(player.x, player.y); ctx.rotate(player.angle);
    if (invincible > 0) { ctx.strokeStyle = dashTime > 0 ? '#f3ffb4' : '#46f8d090'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(0, 0, 25, 0, Math.PI * 2); ctx.stroke(); }
    ctx.shadowColor = '#46f8d0'; ctx.shadowBlur = 20; ctx.fillStyle = '#46f8d0'; ctx.beginPath(); ctx.moveTo(19, 0); ctx.lineTo(-12, -12); ctx.lineTo(-6, 0); ctx.lineTo(-12, 12); ctx.closePath(); ctx.fill(); ctx.shadowBlur = 0;
    ctx.fillStyle = '#e2fff7'; ctx.beginPath(); ctx.moveTo(9, 0); ctx.lineTo(-6, -4); ctx.lineTo(-3, 0); ctx.lineTo(-6, 4); ctx.fill(); ctx.restore();
    if (pointer && state === 'playing') { ctx.strokeStyle = '#46f8d050'; ctx.beginPath(); ctx.arc(pointer.x, pointer.y, 12, 0, Math.PI * 2); ctx.stroke(); }
    ctx.restore();
  }
  function tick(now) { const dt = Math.min((now - last) / 1000 || 0, .035); last = now; if (!reduced) visualTime += dt; if (state === 'playing') update(dt); draw(); requestAnimationFrame(tick); }
  $('start').addEventListener('click', begin); $('pause').addEventListener('click', togglePause); $('dash').addEventListener('click', dash);
  window.addEventListener('keydown', e => {
    const key = e.key.toLowerCase();
    if (['arrowup', 'arrowdown', 'arrowleft', 'arrowright', ' '].includes(key) && (state === 'playing' || e.target === canvas)) e.preventDefault();
    if (key === 'p' || key === 'escape') { if (!e.repeat) togglePause(); return; }
    if (key === ' ' && state === 'playing') { if (!e.repeat) dash(); return; }
    if (state === 'playing') keys.add(key);
  });
  window.addEventListener('keyup', e => keys.delete(e.key.toLowerCase()));
  function movePointer(e) { const r = canvas.getBoundingClientRect(); pointer = { x: (e.clientX - r.left - view.x) / view.scale, y: (e.clientY - r.top - view.y) / view.scale }; }
  canvas.addEventListener('pointerdown', e => { if (state !== 'playing') return; canvas.setPointerCapture(e.pointerId); movePointer(e); canvas.focus(); });
  canvas.addEventListener('pointermove', e => { if (pointer) movePointer(e); });
  for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(event, () => { pointer = null; });
  window.addEventListener('blur', () => { keys.clear(); if (state === 'playing') togglePause(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden && state === 'playing') togglePause(); });
  reset(); resize(); requestAnimationFrame(tick);
})();
