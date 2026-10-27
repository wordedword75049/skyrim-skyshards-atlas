import test from 'node:test';
import assert from 'node:assert/strict';
import {
  defaultFilters, visibleShard, resetFilters, counts, markerClass,
  collectionTransition, readableName, projectWorld, visiblePlayerLocation, saveIndicator,
} from '../src/skyshard_tracker/static/tracker-state.mjs';

const shard = (id, status, hold = 'Falkreath') => ({id, status, hold});
const save = (number, stamp, character = 'TestHero', filename = `Save${number}_1234ABCD_test.ess`) =>
  ({save_number: number, modified_at: stamp, character, filename});
const snapshot = (rows, identity, extra = {}) => ({
  catalog_version: 'catalog', total: 108,
  collected_count: rows.filter(row => row.status === 'collected').length,
  uncollected_count: rows.filter(row => row.status === 'uncollected').length,
  unknown_count: rows.filter(row => row.status === 'unknown').length,
  shards: rows, save: identity, is_newest: true, newer_save_error: null, ...extra,
});

test('terrain projection follows the exact world grid', () => {
  const terrain = [1/64, 0, 4096, 0, -1/64, 4096];
  const point = [174645.109375, -96010.140625];
  assert.deepEqual(projectWorld(terrain, ...point),
    [(point[0] + 262144) / 64, (262144 - point[1]) / 64]);
});

test('status and hold filters start with collected hidden and reset', () => {
  const filters = defaultFilters();
  assert.equal(visibleShard(shard('a', 'collected'), filters), false);
  assert.equal(visibleShard(shard('b', 'uncollected'), filters), true);
  assert.equal(visibleShard(shard('c', 'unknown'), filters), true);
  assert.equal(visibleShard(shard('b', 'uncollected', 'Whiterun'), {...filters, hold: 'Falkreath'}), false);
  assert.equal(visibleShard(shard('b', 'uncollected'), {...filters, uncollected: false}), false);
  assert.equal(visibleShard(shard('c', 'unknown'), {...filters, unknown: false}), false);
  assert.deepEqual(resetFilters(), filters);
});

test('counts and marker classes keep unknown separate', () => {
  const rows = [shard('a', 'collected'), shard('b', 'uncollected'), shard('c', 'unknown')];
  assert.deepEqual(counts(rows), {collected: 1, uncollected: 1, unknown: 1, total: 3, percent: 33});
  assert.equal(markerClass(rows[0]), 'shard-pin collected');
  assert.equal(markerClass(rows[1]), 'shard-pin uncollected');
  assert.equal(markerClass(rows[2]), 'shard-pin unknown');
  assert.equal(readableName('DMN_SkyshardFalkreath04', 'Falkreath'), 'Falkreath · Skyshard 04');
});

test('known 439 to 440 single transition highlights Falkreath04', () => {
  const before = snapshot([shard('DMN_SkyshardFalkreath04', 'uncollected')], save(439, '2026-10-20T14:37:11Z'));
  const after = snapshot([shard('DMN_SkyshardFalkreath04', 'collected')], save(440, '2026-10-20T14:39:05Z'));
  assert.equal(collectionTransition(before, after), 'DMN_SkyshardFalkreath04');
});

test('older saves, character switches, uncertainty, and stale data do not animate', () => {
  const id = 'DMN_SkyshardFalkreath04';
  const before = snapshot([shard(id, 'uncollected')], save(439, '2026-10-20T14:37:11Z'));
  const later = snapshot([shard(id, 'collected')], save(440, '2026-10-20T14:39:05Z'));
  assert.equal(collectionTransition(later, before), null);
  assert.equal(collectionTransition(before, {...later, save: save(440, '2026-10-20T14:39:05Z', 'Other')}), null);
  assert.equal(collectionTransition(before, {...later, save: save(440, '2026-10-20T14:39:05Z', 'TestHero', 'renamed.ess')}), null);
  assert.equal(collectionTransition(before, {...later, is_newest: false}), null);
  assert.equal(collectionTransition({...before, unknown_count: 1}, later), null);
  assert.equal(collectionTransition(before, {...later, newer_save_error: 'incomplete'}), null);
});

test('player position is visible only for a current verified Tamriel exterior save', () => {
  const location = {status: 'available', worldspace: 'Tamriel', is_exterior: true,
    x: -72895.84375, y: -68086.8359375};
  const current = snapshot([], save(439, '2026-10-20T14:37:11Z'), {player_location: location});
  assert.equal(visiblePlayerLocation(current), location);
  assert.equal(visiblePlayerLocation({...current, is_newest: false}), null);
  assert.equal(visiblePlayerLocation({...current, newer_save_error: 'bad save'}), null);
  assert.equal(visiblePlayerLocation({...current, save: null}), null);
  assert.equal(visiblePlayerLocation({...current, player_location: {...location, worldspace: null}}), null);
  assert.equal(visiblePlayerLocation({...current, player_location: {...location, is_exterior: false}}), null);
  assert.equal(visiblePlayerLocation({...current, player_location: {...location, x: NaN}}), null);
  const pixels = projectWorld([1/64, 0, 4096, 0, -1/64, 4096], location.x, location.y);
  assert.deepEqual(pixels, [2957.00244140625, 5159.8568115234375]);
});

test('missing optional co-save is diagnostic, not stale progress', () => {
  const current = snapshot([], save(1, '2026-10-20T14:37:11Z'),
    {warnings: ['Matching SKSE co-save: missing']});
  assert.deepEqual(saveIndicator(current), {state: 'live', text: 'LATEST SAVE'});
  assert.equal(saveIndicator({...current, newer_save_error: 'bad save'}).state, 'stale');
  assert.equal(saveIndicator({...current, unknown_count: 108}).state, 'stale');
});
