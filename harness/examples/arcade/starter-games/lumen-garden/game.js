/* Lumen Garden — original art and game, MIT licensed. No external assets. */
(() => {
  'use strict';
  const DIRS = [[-1, 0, 1, 4], [0, 1, 2, 8], [1, 0, 4, 1], [0, -1, 8, 2]];
  const LEVELS = [
    { n: 3, title: 'First light', note: 'Two sleeping flowers. One small beginning.', paths: [
      [[1,0],[1,1],[0,1],[0,2]], [[1,1],[2,1],[2,2]]
    ] },
    { n: 4, title: 'Winding roots', note: 'Sometimes the light takes the longer way home.', paths: [
      [[3,0],[3,1],[2,1],[2,2],[1,2],[0,2],[0,3]], [[2,2],[2,3],[3,3]], [[2,1],[1,1],[1,0],[0,0]]
    ] },
    { n: 5, title: 'Little branches', note: 'A single stem can hold a whole world of light.', paths: [
      [[2,0],[2,1],[1,1],[1,2],[1,3],[0,3],[0,4]], [[2,1],[3,1],[4,1],[4,0]],
      [[1,3],[2,3],[3,3],[3,4]], [[3,3],[4,3],[4,4]], [[1,1],[0,1],[0,0]]
    ] },
    { n: 5, title: 'The heartwood', note: 'Start at the heart. Let the garden unfold.', paths: [
      [[4,2],[3,2],[2,2],[2,1],[1,1],[1,0],[0,0]], [[2,1],[3,1],[3,0],[4,0]],
      [[2,2],[2,3],[1,3],[1,4],[0,4]], [[2,3],[3,3],[3,4],[4,4]], [[2,2],[1,2],[0,2]]
    ] },
    { n: 6, title: 'Wild abundance', note: 'Follow each fork. There is enough light for all.', paths: [
      [[5,0],[5,1],[4,1],[3,1],[3,2],[2,2],[2,3],[2,4],[1,4],[0,4],[0,5]],
      [[3,1],[2,1],[1,1],[1,0],[0,0]], [[2,2],[1,2],[0,2],[0,3]],
      [[2,4],[3,4],[4,4],[4,5],[5,5]], [[3,4],[3,5],[2,5]], [[3,2],[4,2],[4,3],[5,3]]
    ] },
    { n: 6, title: 'A garden of stars', note: 'One last garden. Make the whole night bloom.', paths: [
      [[0,0],[1,0],[1,1],[2,1],[3,1],[3,2],[3,3],[2,3],[1,3],[1,4],[0,4],[0,5]],
      [[1,1],[0,1],[0,2]], [[2,1],[2,0],[3,0],[4,0],[5,0],[5,1]],
      [[3,2],[4,2],[5,2],[5,3],[5,4],[5,5]], [[3,3],[4,3],[4,4],[4,5],[3,5],[2,5],[1,5]],
      [[2,3],[2,2],[1,2]]
    ] }
  ];
  function rotate(mask) { return ((mask << 1) & 15) | (mask >> 3); }
  function makeLevel(level, scrambled = true) {
    const tiles = Array.from({ length: level.n * level.n }, (_, id) => ({ id, mask: 0, solution: 0, source: false, bloom: false, locked: false }));
    for (const path of level.paths) {
      for (let i = 1; i < path.length; i++) {
        const [r, c] = path[i - 1], [nr, nc] = path[i];
        const direction = DIRS.find(([dr, dc]) => r + dr === nr && c + dc === nc);
        if (!direction) throw new Error('Garden paths must join adjacent cells');
        tiles[r * level.n + c].solution |= direction[2];
        tiles[nr * level.n + nc].solution |= direction[3];
      }
    }
    const [sr, sc] = level.paths[0][0];
    tiles[sr * level.n + sc].source = true;
    for (const tile of tiles) {
      tile.mask = tile.solution;
      tile.bloom = !!tile.mask && !(tile.mask & (tile.mask - 1)) && !tile.source;
      tile.locked = tile.bloom || tile.source || tile.mask === 15 || !tile.mask;
      if (scrambled && !tile.locked) {
        const turns = 1 + ((tile.id * 7 + level.n) % 3);
        for (let k = 0; k < turns; k++) tile.mask = rotate(tile.mask);
      }
    }
    return tiles;
  }
  function lightNetwork(tiles, n) {
    const source = tiles.find(t => t.source);
    const lit = new Set(source ? [source.id] : []), queue = source ? [source.id] : [];
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i], r = Math.floor(id / n), c = id % n;
      for (const [dr, dc, bit, opposite] of DIRS) {
        const nr = r + dr, nc = c + dc;
        if (!(tiles[id].mask & bit) || nr < 0 || nc < 0 || nr >= n || nc >= n) continue;
        const next = nr * n + nc;
        if ((tiles[next].mask & opposite) && !lit.has(next)) { lit.add(next); queue.push(next); }
      }
    }
    return lit;
  }
  if (typeof document === 'undefined') {
    if (typeof module !== 'undefined') module.exports = { LEVELS, makeLevel, lightNetwork, rotate };
    return;
  }
  const $ = id => document.getElementById(id);
  const canvas = $('canvas'), ctx = canvas.getContext('2d');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = canvas.height = 640 * dpr;
  ctx.scale(dpr, dpr);
  let current = 0, highest = 0, moves = 0, active = false, won = false, hovered = -1;
  let tiles = makeLevel(LEVELS[3], false), n = 5, lit = lightNetwork(tiles, n);
  let wonAt = 0, lastFrame = 0;
  const completed = new Set();
  const controls = new Map();
  const names = ['north', 'east', 'south', 'west'];
  function label(tile) {
    const ends = DIRS.filter(d => tile.mask & d[2]).map(d => names[DIRS.indexOf(d)]).join(' and ');
    return `Row ${Math.floor(tile.id / n) + 1}, column ${tile.id % n + 1}: ${lit.has(tile.id) ? 'lit' : 'unlit'} stem, ends ${ends}. Rotate clockwise.`;
  }
  function updateChapters() {
    $('chapters').replaceChildren();
    LEVELS.forEach((level, index) => {
      const b = document.createElement('button'); b.className = `chapter${index === current ? ' current' : ''}${completed.has(index) ? ' completed' : ''}`;
      b.textContent = String(index + 1); b.disabled = index > highest;
      b.setAttribute('aria-label', `Garden ${index + 1}: ${level.title}${completed.has(index) ? ', completed' : ''}`);
      if (index === current) b.setAttribute('aria-current', 'step');
      b.addEventListener('click', () => load(index)); $('chapters').appendChild(b);
    });
  }
  function load(index) {
    current = index; active = true; won = false; moves = 0; hovered = -1;
    const level = LEVELS[index]; n = level.n; tiles = makeLevel(level); lit = lightNetwork(tiles, n);
    document.body.classList.add('playing'); $('welcome').hidden = true; $('playing').hidden = false; $('next').hidden = true;
    $('eyebrow').textContent = `GARDEN ${String(index + 1).padStart(2, '0')} OF 06`;
    $('heading').textContent = level.title; $('description').textContent = level.note;
    $('board-label').textContent = 'FOLLOW THE GOLDEN LIGHT';
    $('board-tip').textContent = 'Tap a stem to turn it. Let every flower glow.';
    $('status').textContent = index === 0 ? 'The sun and flowers stay still. Turn the three stems between them.' : 'Work outward from the sun, one connection at a time.';
    $('hint').disabled = false;
    controls.clear(); $('tile-controls').replaceChildren();
    const step = 552 / n;
    for (const tile of tiles.filter(t => !t.locked)) {
      const button = document.createElement('button'); button.className = 'tile'; button.dataset.tile = tile.id;
      Object.assign(button.style, { left: `${(44 + (tile.id % n) * step) / 6.4}%`, top: `${(44 + Math.floor(tile.id / n) * step) / 6.4}%`, width: `${step / 6.4}%`, height: `${step / 6.4}%` });
      button.addEventListener('click', () => turn(tile.id));
      button.addEventListener('pointerenter', () => { hovered = tile.id; });
      button.addEventListener('pointerleave', () => { hovered = -1; });
      button.addEventListener('focus', () => { hovered = tile.id; });
      button.addEventListener('blur', () => { hovered = -1; });
      button.addEventListener('keydown', event => {
        const dir = { ArrowUp: [-1,0], ArrowRight: [0,1], ArrowDown: [1,0], ArrowLeft: [0,-1] }[event.key];
        if (!dir) return;
        event.preventDefault();
        const row = Math.floor(tile.id / n), col = tile.id % n;
        const candidates = [...controls.keys()].filter(id => dir[0] ? (Math.floor(id / n) - row) * dir[0] > 0 : ((id % n) - col) * dir[1] > 0);
        candidates.sort((a,b) => {
          const distance = id => Math.abs(Math.floor(id / n) - row) * (dir[0] ? 1 : 8) + Math.abs(id % n - col) * (dir[1] ? 1 : 8);
          return distance(a) - distance(b);
        });
        if (candidates.length) controls.get(candidates[0]).focus();
      });
      controls.set(tile.id, button); $('tile-controls').appendChild(button);
    }
    updateStats(); updateChapters();
  }
  function updateStats() {
    const flowers = tiles.filter(t => t.bloom), count = flowers.filter(t => lit.has(t.id)).length;
    $('blooms').textContent = `${count} / ${flowers.length}`; $('moves').textContent = moves;
    const position = tile => `row ${Math.floor(tile.id / n) + 1}, column ${tile.id % n + 1}`;
    canvas.setAttribute('aria-label', `${n} by ${n} garden. Sun at ${position(tiles.find(t => t.source))}. Flowers at ${flowers.map(t => `${position(t)}, ${lit.has(t.id) ? 'awake' : 'sleeping'}`).join('; ')}. ${count} of ${flowers.length} blooms awake. Tab to the stems to rotate them.`);
    for (const [id, button] of controls) button.setAttribute('aria-label', label(tiles[id]));
    if (active && count === flowers.length && !won) {
      won = true; wonAt = performance.now(); completed.add(current); highest = Math.max(highest, Math.min(current + 1, LEVELS.length - 1));
      $('status').textContent = current === 5 ? `Every garden is awake. You brought the night to life in ${moves} turns in this garden.` : `All ${count} flowers are awake. A little light goes a long way.`;
      $('board-label').textContent = 'A GARDEN, FULL OF LIGHT'; $('board-tip').textContent = 'Take a breath. Look what you grew.';
      $('next').hidden = false; $('next').innerHTML = current === 5 ? 'Grow it again <span aria-hidden="true">↺</span>' : 'Next garden <span aria-hidden="true">→</span>';
      $('hint').disabled = true; for (const b of controls.values()) b.disabled = true;
      updateChapters(); $('next').focus({ preventScroll: true });
    }
  }
  function turn(id) {
    if (!active || won || tiles[id].locked) return;
    const before = tiles.filter(t => t.bloom && lit.has(t.id)).length;
    tiles[id].mask = rotate(tiles[id].mask); moves++; lit = lightNetwork(tiles, n);
    const after = tiles.filter(t => t.bloom && lit.has(t.id)).length;
    if (after > before) $('status').textContent = 'A new bloom opens. Keep following the light.';
    updateStats();
  }
  $('start').addEventListener('click', () => { load(0); controls.values().next().value?.focus({ preventScroll: true }); });
  $('reset').addEventListener('click', () => { load(current); $('status').textContent = 'A fresh beginning. The garden is ready when you are.'; });
  $('next').addEventListener('click', () => { load((current + 1) % LEVELS.length); controls.values().next().value?.focus({ preventScroll: true }); });
  $('hint').addEventListener('click', () => {
    if (won) return;
    const tile = tiles.find(t => !t.locked && t.mask !== t.solution);
    if (tile) {
      tile.mask = tile.solution; moves++; lit = lightNetwork(tiles, n);
      $('status').textContent = `A little nudge: the stem in row ${Math.floor(tile.id / n) + 1}, column ${tile.id % n + 1} now points the right way.`;
      updateStats(); if (!won) controls.get(tile.id).focus({ preventScroll: true });
    }
  });
  $('home').addEventListener('click', e => {
    e.preventDefault(); active = false; won = false; n = 5; tiles = makeLevel(LEVELS[3], false); lit = lightNetwork(tiles, n);
    document.body.classList.remove('playing'); $('welcome').hidden = false; $('playing').hidden = true; $('tile-controls').replaceChildren(); controls.clear();
    $('eyebrow').textContent = 'A LITTLE LIGHT. A LITTLE WONDER.'; $('heading').innerHTML = 'Let your<br>garden <em>glow.</em>';
    $('description').textContent = 'Turn the stems. Follow the light. Bring a sleeping garden back to life, one small bloom at a time.';
    $('board-label').textContent = 'THE NIGHT GARDEN'; $('board-tip').textContent = 'A small world, waiting to wake.'; $('start').focus();
  });
  $('help').addEventListener('click', () => $('help-dialog').showModal());
  $('close-help').addEventListener('click', () => $('help-dialog').close());
  $('got-it').addEventListener('click', () => $('help-dialog').close());
  document.addEventListener('keydown', e => {
    if (e.key.toLowerCase() === 'r' && !e.ctrlKey && !e.metaKey && !e.altKey && active && !$('help-dialog').open) { e.preventDefault(); load(current); controls.values().next().value?.focus({ preventScroll: true }); }
  });
  function line(x1,y1,x2,y2,color,width) { ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.strokeStyle=color; ctx.lineWidth=width; ctx.stroke(); }
  function leaf(x,y,size,angle,color) {
    ctx.save(); ctx.translate(x,y); ctx.rotate(angle); ctx.beginPath(); ctx.moveTo(0,0); ctx.quadraticCurveTo(size,-size*.9,size*1.7,0); ctx.quadraticCurveTo(size,size*.7,0,0); ctx.fillStyle=color; ctx.fill(); ctx.restore();
  }
  function flower(x,y,size,on,time,id) {
    const pulse = reduced ? 1 : 1 + Math.sin(time*.0014+id)*.035;
    ctx.save(); ctx.translate(x,y); ctx.scale(pulse,pulse);
    if (on) { ctx.shadowColor='#f1ce83'; ctx.shadowBlur=19; }
    for(let p=0;p<6;p++) { ctx.save();ctx.rotate(p*Math.PI/3);ctx.beginPath();ctx.ellipse(0,-size*.63,size*.35,size*.61,0,0,Math.PI*2);ctx.fillStyle=on?'#e9d79d':'#517967';ctx.fill();ctx.restore(); }
    ctx.shadowBlur=0;ctx.beginPath();ctx.arc(0,0,size*.3,0,Math.PI*2);ctx.fillStyle=on?'#f9edbf':'#173d34';ctx.fill();ctx.restore();
  }
  function draw(time) {
    ctx.clearRect(0,0,640,640); ctx.lineCap='round'; ctx.lineJoin='round';
    const step=552/n;
    // Fine constellation dots and corner filigree frame the actual puzzle.
    for(let r=0;r<12;r++) for(let c=0;c<12;c++) { ctx.fillStyle='#76a08a1a';ctx.beginPath();ctx.arc(26+c*53.5,26+r*53.5,1,0,Math.PI*2);ctx.fill(); }
    for(let corner=0;corner<4;corner++) {ctx.save();ctx.translate(corner%2?615:25,corner>1?615:25);ctx.scale(corner%2?-1:1,corner>1?-1:1);line(0,22,0,0,'#7e9c6e70',1);line(0,0,22,0,'#7e9c6e70',1);leaf(5,5,8,Math.PI/4,'#7e9c6e65');ctx.restore();}
    for (const tile of tiles) {
      const x=44+(tile.id%n+.5)*step,y=44+(Math.floor(tile.id/n)+.5)*step,on=lit.has(tile.id),size=step*.49;
      if (!tile.mask) {ctx.save();ctx.globalAlpha=.28;leaf(x-5,y+7,step*.12,-1.3,'#7b9c76');leaf(x-5,y+7,step*.09,-2.6,'#7b9c76');ctx.restore();continue;}
      ctx.beginPath();ctx.roundRect(x-size+3,y-size+3,size*2-6,size*2-6,12);ctx.fillStyle=on?'#d6c18108':'#0b252525';ctx.fill();ctx.strokeStyle=on?'#d2bd7920':'#6b9c7514';ctx.lineWidth=1;ctx.stroke();
      const color=on?'#e2bd75':'#436b59';
      ctx.shadowColor='#e6ba70';ctx.shadowBlur=on?10:0;
      for (const [dr,dc,bit] of DIRS) if(tile.mask&bit) line(x,y,x+dc*step*.5,y+dr*step*.5,color,Math.max(4,step*.045));
      ctx.shadowBlur=0;
      if (!tile.source && !tile.bloom) {
        ctx.beginPath();ctx.arc(x,y,step*.068,0,Math.PI*2);ctx.fillStyle=on?'#eed399':'#587b62';ctx.fill();
        const first=DIRS.find(d=>tile.mask&d[2]);const angle=Math.atan2(first[0],first[1]);
        leaf(x+Math.cos(angle)*step*.21,y+Math.sin(angle)*step*.21,step*.105,angle-.8,on?'#a6ae72':'#3e6854');
        if(tile.id===hovered&&!won&&active){ctx.beginPath();ctx.arc(x,y,step*.19,-.7,2);ctx.strokeStyle='#edd29a99';ctx.lineWidth=1.5;ctx.stroke();}
      }
      if(tile.bloom)flower(x,y,step*.17,on,time,tile.id);
      if(tile.source){
        ctx.shadowColor='#f7be63';ctx.shadowBlur=26;ctx.beginPath();ctx.arc(x,y,step*.125,0,Math.PI*2);ctx.fillStyle='#f1cd85';ctx.fill();ctx.shadowBlur=0;
        ctx.beginPath();ctx.arc(x,y,step*.19,0,Math.PI*2);ctx.strokeStyle='#e9bd6f65';ctx.lineWidth=1;ctx.stroke();
        for(let k=0;k<12;k++){const a=k*Math.PI/6;line(x+Math.cos(a)*step*.23,y+Math.sin(a)*step*.23,x+Math.cos(a)*step*.27,y+Math.sin(a)*step*.27,'#e6bb6b',1.5);}
      }
    }
    if(!reduced){
      for(let i=0;i<14;i++){const x=40+((i*137.5+Math.sin(time*.0003+i)*13)%560),y=35+((i*83.7-time*.007)%570+570)%570;ctx.globalAlpha=.15+Math.sin(time*.001+i)*.12;ctx.fillStyle='#f2d795';ctx.beginPath();ctx.arc(x,y,1.8,0,Math.PI*2);ctx.fill();}ctx.globalAlpha=1;
      if(won&&time-wonAt<2300){const f=(time-wonAt)/2300;ctx.globalAlpha=1-f;for(let i=0;i<40;i++){const a=i*2.399,r=50+f*(140+(i%5)*24);ctx.fillStyle=i%2?'#edcd86':'#a6c99a';ctx.beginPath();ctx.arc(320+Math.cos(a)*r,320+Math.sin(a)*r+f*f*55,2.5,0,Math.PI*2);ctx.fill();}ctx.globalAlpha=1;}
    }
  }
  function frame(time){if(time-lastFrame>=32){draw(time);lastFrame=time;}requestAnimationFrame(frame);}
  requestAnimationFrame(frame);
})();
