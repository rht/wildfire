import { writeFileSync } from "node:fs";
import { demoIncidents } from "../src/state/demo.mjs";
// Deliberately illustrative drill history. Never a record of real calls or deployments.
const times = [
  "2026-09-20T09:45:00Z",
  "2026-09-20T10:00:00Z",
  "2026-09-20T10:15:00Z",
];
const entries = times.map((as_of, stage) => ({
  as_of,
  incidents: structuredClone(demoIncidents.slice(0, stage + 1)).map(
    (incident) => {
      incident.as_of = as_of;
      incident.snapshot_id = `${incident.id}-illustrative-history-${stage + 1}`;
      incident.revision = stage + 1;
      incident.input_mode = "offline_demo";
      // These demo polygons are convex. Scale about the mean of their distinct
      // vertices so successive stages are nested and share a fixed centre.
      // The fourth (current) moment keeps the original demo perimeter unchanged.
      const linearScale = (stage + 1) / (times.length + 1);
      const vertices = incident.fire_geometry.coordinates[0].slice(0, -1);
      const centre = [0, 1].map(
        (axis) =>
          vertices.reduce((sum, point) => sum + point[axis], 0) /
          vertices.length,
      );
      incident.fire_geometry.coordinates =
        incident.fire_geometry.coordinates.map((ring) =>
          ring.map((point) =>
            point.map(
              (value, axis) =>
                centre[axis] + (value - centre[axis]) * linearScale,
            ),
          ),
        );
      incident.fire_simulation = {
        source: "Illustrative simulated spread",
        as_of,
        stage: stage + 1,
        total_stages: times.length + 1,
        linear_scale: linearScale,
        method:
          "Deterministic scaling of the design demo perimeter about its vertex centre.",
        notes:
          "Illustrative simulated spread, not a predictive fire model or live observation. Risk values and distances are illustrative and are not recomputed from the scaled perimeter.",
      };
      incident.assets = incident.assets
        .slice(0, stage === 0 ? 2 : 4)
        .map((asset) => ({
          ...asset,
          assessed_at: as_of,
          sources: [
            {
              source: "Generated demo assessment",
              observed_at: as_of,
              fields: ["risk", "valuation", "distance_to_fire_m"],
              notes:
                "Illustrative historical fixture; not an operational observation.",
            },
          ],
        }));
      const known = new Set(incident.assets.map((asset) => asset.asset_id));
      incident.calls = [];
      incident.contacts.ranked = incident.contacts.ranked.filter((row) =>
        known.has(row.asset_id),
      );
      incident.contacts.review = incident.contacts.review.filter((row) =>
        known.has(row.asset_id),
      );
      incident.plan.locations = incident.plan.locations
        .filter((row) => known.has(row.asset_id))
        .map((row) => ({
          asset_id: row.asset_id,
          mode: "undetermined",
          evacuation_status: "not_confirmed",
          reasons: [],
          destination_name: null,
        }));
      incident.plan.response = null;
      incident.resources = [];
      incident.teams = [];
      incident.peopleClusters = [];
      incident.events = [
        {
          event_id: `generated-history-${stage + 1}-${incident.id}`,
          as_of,
          received_at: as_of,
          kind: "assessment",
          source: "Generated demo assessment",
          notes:
            "Generated demo assessment with illustrative simulated spread. Not a predictive fire model or live observation. No calls or dispatch occurred.",
        },
      ];
      return incident;
    },
  ),
}));
writeFileSync(
  new URL("../fixtures/design-history.json", import.meta.url),
  JSON.stringify(
    {
      schema_version: "dashboard-history-1",
      source: "design_demo",
      generated: true,
      entries,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  `Generated ${entries.length} persisted demo moments; the current design dataset is appended at build time.`,
);
