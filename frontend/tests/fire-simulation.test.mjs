import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { demoIncidents } from "../src/state/demo.mjs";
import { historyEntry, mergeHistory } from "../src/state/history-store.mjs";

const fixture = JSON.parse(
  readFileSync(
    new URL("../fixtures/design-history.json", import.meta.url),
    "utf8",
  ),
);

// Triangulate relative to the first vertex to avoid cancellation at map coordinates.
function area(ring) {
  const [x, y] = ring[0];
  return (
    Math.abs(
      ring.slice(1, -1).reduce((sum, point, index) => {
        const next = ring[index + 2];
        return (
          sum + (point[0] - x) * (next[1] - y) - (next[0] - x) * (point[1] - y)
        );
      }, 0),
    ) / 2
  );
}

function centre(ring) {
  const vertices = ring.slice(0, -1);
  return [0, 1].map(
    (axis) =>
      vertices.reduce((sum, point) => sum + point[axis], 0) / vertices.length,
  );
}

// Even-odd ray cast, so the demo perimeters may be concave like a real fire.
function inside([x, y], ring) {
  let odd = false;
  for (let i = 0, j = ring.length - 2; i < ring.length - 1; j = i++) {
    const [ax, ay] = ring[i],
      [bx, by] = ring[j];
    if (ay > y !== by > y && x < ((bx - ax) * (y - ay)) / (by - ay) + ax) {
      odd = !odd;
    }
  }
  return odd;
}

test("persisted demo perimeters expand visibly around a fixed centre into the current perimeter", () => {
  const entries = mergeHistory([
    ...fixture.entries.map((row) => historyEntry(row.incidents)),
    historyEntry(demoIncidents),
  ]);
  assert.equal(entries.length, 4);
  for (const current of demoIncidents) {
    const finalRing = current.fire_geometry.coordinates[0];
    const finalCentre = centre(finalRing);
    const stages = entries.flatMap((entry) =>
      entry.incidents.filter((incident) => incident.id === current.id),
    );
    assert.ok(stages.length >= 2, `${current.id} must expand at least once`);
    let previousRing;
    for (const incident of stages) {
      const geometry = incident.fire_geometry;
      assert.equal(geometry.type, "Polygon");
      const ring = geometry.coordinates[0];
      assert.deepEqual(ring.at(-1), ring[0], "GeoJSON ring stays closed");
      assert.ok(
        ring.length - 1 >= 24,
        "perimeter has an organic outline rather than a four-cornered box",
      );
      assert.ok(
        ring.every(
          ([lon, lat]) =>
            Number.isFinite(lon) &&
            Number.isFinite(lat) &&
            Math.abs(lon) <= 180 &&
            Math.abs(lat) <= 90,
        ),
      );
      centre(ring).forEach((value, axis) =>
        assert.ok(
          Math.abs(value - finalCentre[axis]) < 1e-10,
          "centre stays fixed",
        ),
      );
      assert.ok(area(ring) > 0);
      if (previousRing) {
        assert.ok(
          area(ring) > area(previousRing) * 1.5,
          `${current.id} must visibly grow between recorded moments`,
        );
        assert.ok(
          previousRing.every((point) => inside(point, ring)),
          "each new perimeter contains the entire previous perimeter",
        );
      }
      previousRing = ring;
    }
    assert.deepEqual(stages.at(-1).fire_geometry, current.fire_geometry);
  }
});

test("generated spread stages carry contemporaneous simulation provenance without operations", () => {
  for (const entry of fixture.entries) {
    for (const incident of entry.incidents) {
      assert.equal(incident.input_mode, "offline_demo");
      assert.ok(
        incident.fire_simulation,
        "scaled geometry must carry simulation provenance",
      );
      assert.equal(incident.fire_simulation.as_of, entry.as_of);
      assert.ok(
        incident.fire_simulation.linear_scale > 0 &&
          incident.fire_simulation.linear_scale < 1,
      );
      assert.deepEqual(incident.calls, []);
      assert.deepEqual(incident.resources, []);
      assert.deepEqual(incident.teams, []);
      assert.deepEqual(incident.peopleClusters, []);
      assert.equal(incident.plan.response, null);
      for (const event of incident.events) {
        assert.equal(event.as_of, entry.as_of);
        assert.ok(["assessment", "fire_update"].includes(event.kind));
      }
      for (const asset of incident.assets) {
        assert.equal(asset.assessed_at, entry.as_of);
        for (const source of asset.sources) {
          assert.equal(source.observed_at, entry.as_of);
        }
      }
      for (const location of incident.plan.locations) {
        assert.equal(location.mode, "undetermined");
        assert.equal(location.evacuation_status, "not_confirmed");
        assert.equal(location.destination_name, null);
      }
    }
  }
});
