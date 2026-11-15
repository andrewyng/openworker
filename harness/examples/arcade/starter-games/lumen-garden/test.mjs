// Run with: node --test starter-games/lumen-garden/test.mjs
// Development checks only; this file is deliberately excluded from manifest.files.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('./game.js', import.meta.url), 'utf8');
const sandbox = { module: { exports: {} } };
runInNewContext(source, sandbox);
const { LEVELS, makeLevel, lightNetwork, rotate } = sandbox.module.exports;

test('four clockwise turns restore every possible stem', () => {
  for (let mask = 0; mask < 16; mask++) {
    assert.equal(rotate(rotate(rotate(rotate(mask)))), mask);
    assert.equal(rotate(mask).toString(2).replaceAll('0', '').length, mask.toString(2).replaceAll('0', '').length);
  }
});

test('light requires reciprocal ends and cannot wrap around a row', () => {
  const tiles = [
    { id: 0, mask: 2, source: true }, { id: 1, mask: 2 },
    { id: 2, mask: 8 }, { id: 3, mask: 0 },
  ];
  assert.deepEqual([...lightNetwork(tiles, 2)], [0]);
  tiles[1].mask = 10;
  assert.deepEqual([...lightNetwork(tiles, 2)], [0, 1]);
});

for (const [index, level] of LEVELS.entries()) {
  test(`garden ${index + 1}: ${level.title} has a reachable, nontrivial solution`, () => {
    const solved = makeLevel(level, false);
    const active = solved.filter(tile => tile.mask);
    const flowers = solved.filter(tile => tile.bloom);
    assert.equal(solved.filter(tile => tile.source).length, 1);
    assert(flowers.length >= 2);
    assert.equal(lightNetwork(solved, level.n).size, active.length, 'every designed stem and flower connects to the sun');

    const scrambled = makeLevel(level);
    const initialLight = lightNetwork(scrambled, level.n);
    assert(flowers.some(tile => !initialLight.has(tile.id)), 'the initial garden must not already be solved');
    let turns = 0;
    for (const tile of scrambled) {
      if (tile.locked) assert.equal(tile.mask, tile.solution, 'sun, flowers, and fixed junctions remain aligned');
      let rotations = 0;
      while (tile.mask !== tile.solution && rotations < 4) {
        tile.mask = rotate(tile.mask);
        rotations++; turns++;
      }
      assert(rotations < 4, 'the intended orientation is reachable by rotation');
      assert.equal(tile.mask, tile.solution);
    }
    assert(turns > 0);
    const finalLight = lightNetwork(scrambled, level.n);
    assert(flowers.every(tile => finalLight.has(tile.id)), 'all flowers wake after the solution turns');
    assert.equal(finalLight.size, active.length);
  });
}

test('published bundle is complete and within size limits', () => {
  const manifest = JSON.parse(readFileSync(new URL('./manifest.json', import.meta.url), 'utf8'));
  assert.equal(manifest.id, 'lumen-garden');
  assert.equal(manifest.engine, 'canvas');
  assert.equal(manifest.license, 'MIT');
  assert(!manifest.files.includes('test.mjs'));
  let total = 0;
  for (const file of manifest.files) {
    const size = statSync(new URL(file, import.meta.url)).size;
    assert(size < 90_000, `${file} exceeds the individual file limit`);
    total += size;
  }
  assert(total < 600_000);
});
