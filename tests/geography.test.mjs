import test from 'node:test';
import assert from 'node:assert/strict';
import {featureVisible, pickLabels} from '../src/skyshard_tracker/static/geography-state.mjs';

test('cities remain available; landmark sites and regional names respect layers and zoom', () => {
  const city = {kind: 'city', name: 'Whiterun'};
  const village = {kind: 'settlement', name: 'Riverwood'};
  const landmark = {kind: 'landmark', name: 'High Hrothgar'};
  assert.equal(featureVisible(city, -3, false), true);
  assert.equal(featureVisible(village, -3, true), false);
  assert.equal(featureVisible(village, -1, true), true);
  assert.equal(featureVisible(landmark, 0, false), false);
  assert.equal(featureVisible(landmark, 0, true), true);
});

test('label collision keeps higher-priority name and permits separated labels', () => {
  const rows = [
    {id: 'city', kind: 'city', name: 'Whiterun', x: 50, y: 50},
    {id: 'site', kind: 'landmark', name: 'Dragonsreach', x: 55, y: 52},
    {id: 'far', kind: 'settlement', name: 'Riverwood', x: 300, y: 200},
  ];
  assert.deepEqual(pickLabels(rows, 500, 300), ['city', 'far']);
  assert.deepEqual(pickLabels(rows, 500, 300, false), []);
});
