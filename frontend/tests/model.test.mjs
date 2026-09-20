import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
const path = new URL("../src/state/model.mjs", import.meta.url);
test("dashboard adapter provides tested data boundaries", async (t) => {
  assert.ok(fs.existsSync(path), "dashboard model must exist");
  const {
    toIncident,
    metrics,
    callRows,
    buildingRows,
    filterBuildings,
    orderedEvents,
    evacuationTotals,
  } = await import(path);
  const state = {
    scenario_id: "gavarres",
    snapshot_id: "s1",
    as_of: "2026-09-20T10:00:00Z",
    assets: [
      {
        asset_id: "a",
        name: "School",
        estimated_occupancy: 90,
        value_score: 5,
        burn_probability: 0,
        replacement_value_eur: 200000,
      },
      { asset_id: "b", name: "Care home", estimated_occupancy: null },
    ],
    contacts: {
      ranked: [
        { asset_id: "b", rank: 1, slack_min: 5 },
        { asset_id: "a", rank: 2, slack_min: 10 },
      ],
      review: [],
    },
    calls: [
      {
        request_id: "r1",
        asset_id: "a",
        status: "completed",
        can_self_evacuate: true,
      },
      {
        request_id: "r2",
        asset_id: "a",
        status: "completed",
        wants_human: true,
        human_followup_reasons: ["inconclusive"],
      },
    ],
    teams: [{ team_id: "t1", available: false }],
    plan: { locations: [], response: null },
    events: [],
  };
  const i = toIncident(state);
  await t.test(
    "unknown counts stay unknown; roster and occupancy never prove deployment or evacuation",
    () => {
      assert.deepEqual(metrics([i]), {
        active: null,
        deployed: null,
        structures: 2,
        clusters: null,
      });
      assert.equal(evacuationTotals(i).selfEvacuating, null);
    },
  );
  await t.test(
    "contacts count locations once, preserve backend order and expose all reasons",
    () => {
      const rows = callRows(i);
      assert.deepEqual(
        rows.map((r) => r.asset_id),
        ["b", "a"],
      );
      assert.equal(rows.filter((r) => r.called).length, 1);
      assert.equal(rows.filter((r) => r.toCall).length, 1);
      assert.ok(rows[1].reasons.includes("Human requested"));
      assert.ok(rows[1].reasons.includes("Inconclusive"));
    },
  );
  await t.test(
    "risk score never comes from value score; zero probability remains zero",
    () => {
      const rows = buildingRows([i]);
      assert.equal(rows[0].risk_score, null);
      assert.equal(rows[0].burn_probability, 0);
      assert.equal(rows[0].replacement_value_eur, 200000);
    },
  );
  await t.test(
    "building filters combine incident, assessment date, type and search",
    () => {
      const other = toIncident({
        ...state,
        scenario_id: "other",
        as_of: "2026-09-18T10:00:00Z",
      });
      assert.equal(
        filterBuildings(buildingRows([i, other]), {
          incident: "gavarres",
          from: "2026-09-20",
          to: "2026-09-20",
          search: "school",
        }).length,
        1,
      );
      assert.equal(
        filterBuildings(buildingRows([i, other]), { from: "2026-09-21" })
          .length,
        0,
      );
    },
  );
  await t.test(
    "arrival order uses ingestion sequence, not event time; equal timestamps are stable",
    () => {
      const eventIncident = {
        ...i,
        events: [
          {
            event_id: "late",
            ingestion_sequence: 2,
            received_at: "2026-09-20T10:01:00Z",
            as_of: "2026-09-19",
            kind: "call",
          },
          {
            event_id: "first",
            ingestion_sequence: 1,
            received_at: "2026-09-20T10:01:00Z",
            as_of: "2026-09-20",
            kind: "fire",
          },
        ],
      };
      assert.deepEqual(
        orderedEvents([eventIncident], {}).map((e) => e.event_id),
        ["late", "first"],
      );
      assert.deepEqual(
        orderedEvents([eventIncident], { order: "oldest" }).map(
          (e) => e.event_id,
        ),
        ["first", "late"],
      );
      assert.equal(orderedEvents([eventIncident], { kind: "call" }).length, 1);
    },
  );
});
