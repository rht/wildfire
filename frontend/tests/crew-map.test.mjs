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

test('planned departure stays distinct from observed current location and has a shared row ID', () => {
  const data = crewMapData({
    starting_location: { name: 'Station', latitude: 40, longitude: 1, source: 'Planner' },
    current_location: { latitude: 41, longitude: 2, source: 'GPS', observed_at: '2026-09-20T10:00:00Z' },
  });
  assert.equal(data.start.id, '__start__');
  assert.equal(data.start.kind, 'planned');
  assert.equal(data.start.name, 'Station');
  assert.deepEqual(data.start.position, [40, 1]);
  assert.deepEqual(data.current.position, [41, 2]);
  assert.equal(data.start.source, 'Planner');
});

test('only supplied valid locations establish a start, with reported location labelled explicitly', () => {
  const observed = crewMapData({ current_location: { latitude: 41, longitude: 2 } });
  assert.equal(observed.start.kind, 'reported');
  assert.equal(observed.start.id, '__start__');
  assert.deepEqual(observed.start.position, [41, 2]);
  const unknown = crewMapData({ starting_location: { latitude: 91, longitude: 2 }, start_node: [2, 41], tasks: [{ path_lonlat: [[2, 41], [3, 42]] }] });
  assert.equal(unknown.start, null);
  assert.equal(unknown.current, null);
  assert.equal(crewMapData().start, null);
});

test('planning context supplies a planned departure without claiming an observed position', () => {
  const data = crewMapData({ planning_context: { start: { node_id: 'station', name: 'Station', latitude: 41, longitude: 2, source: 'Operations' } } });
  assert.equal(data.start?.kind, 'planned');
  assert.equal(data.start?.name, 'Station');
  assert.deepEqual(data.start?.position, [41, 2]);
  assert.equal(data.current, null);
});

test('co-located stops retain distinct stable action IDs and fallback IDs match table helpers', async () => {
  const { crewStopId } = await import('../src/state/crew-map.mjs');
  const tasks = [{ action_id: 'collect', asset_id: 'a' }, { action_id: 'assist', asset_id: 'a' }, { asset_id: 'a' }, { asset_id: 'a' }];
  const data = crewMapData({ tasks }, [{ asset_id: 'a', latitude: 41, longitude: 2 }]);
  assert.deepEqual(data.stops.map(s => s.id), ['collect', 'assist', '__stop__:a:2', '__stop__:a:3']);
  assert.deepEqual(data.stops.map(s => s.id), tasks.map(crewStopId));
  assert.equal(new Set(data.stops.map(s => s.id)).size, 4);
  assert.deepEqual(crewMapData({ tasks: [tasks[1], tasks[0]] }).stops.map(s => s.id), ['assist', 'collect']);
});

test('route arrows follow supplied path bends and omit stationary or unknown legs', async () => {
  const { crewRouteArrows } = await import('../src/state/crew-map.mjs');
  assert.deepEqual(crewRouteArrows([[41, 2], [41, 4], [43, 4]]), [
    { position: [41, 3], rotation: 0 },
    { position: [42, 4], rotation: -90 },
  ]);
  assert.deepEqual(crewRouteArrows([[41, 2], [41, 2]]), []);
  assert.deepEqual(crewRouteArrows([]), []);
  assert.deepEqual(crewRouteArrows([[41, 2]]), []);
});
