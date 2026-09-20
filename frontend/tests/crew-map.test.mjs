import test from 'node:test';
import assert from 'node:assert/strict';
import { crewMapData } from '../src/state/crew-map.mjs';

test('crew map keeps task order, converts lon/lat paths and uses reported position only', () => {
  const data = crewMapData({
    current_location: { latitude: 41, longitude: 2, observed_at: '2026-09-20T10:00:00Z', source: 'Operations' },
    tasks: [
      { asset_id: 'b', start_min: 20, path_lonlat: [[2, 41], [2.2, 41.2]] },
      { asset_id: 'a', start_min: 10, path_lonlat: [[2.2, 41.2], [2.3, 41.3]] },
    ],
  }, [{ asset_id: 'b', name: 'First stop', latitude: 41.2, longitude: 2.2 }]);
  assert.deepEqual(data.current.position, [41, 2]);
  assert.equal(data.current.source, 'Operations');
  assert.deepEqual(data.stops.map(s => [s.number, s.name, s.position]), [
    [1, 'First stop', [41.2, 2.2]], [2, 'a', [41.3, 2.3]],
  ]);
  assert.deepEqual(data.stops[0].path, [[41, 2], [41.2, 2.2]]);
  assert.equal(data.stops[1].positionSource, 'Supplied route endpoint');
});

test('missing or invalid location never falls back to start_node or route origin', () => {
  for (const current_location of [null, { latitude: 91, longitude: 2 }, { latitude: '41', longitude: 2 }]) {
    const data = crewMapData({ current_location, start_node: [2, 41], tasks: [{ path_lonlat: [[2, 41], [3, 42]] }] });
    assert.equal(data.current, null);
    assert.equal(data.stops[0].number, 1);
  }
});

test('invalid path is omitted whole without bridging missing segments or inventing destinations', () => {
  const data = crewMapData({ tasks: [
    { asset_id: 'a', path_lonlat: [[2, 41], null, [3, 42]] },
    { asset_id: 'b', path_lonlat: [[2, 41], [181, 42]] },
    { asset_id: 'a' },
  ] }, [{ asset_id: 'a', name: 'Known destination', latitude: 42, longitude: 3 }]);
  assert.deepEqual(data.stops.map(s => s.path), [[], [], []]);
  assert.deepEqual(data.stops.map(s => s.position), [[42, 3], null, [42, 3]]);
  assert.deepEqual(data.stops.map(s => s.number), [1, 2, 3]);
});
