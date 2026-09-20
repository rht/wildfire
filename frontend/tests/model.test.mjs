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
    readinessFacts,
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
    "contact and plan review reasons stay visible without creating uncalled follow-up",
    () => {
      const reviewIncident = toIncident({
        ...state,
        contacts: {
          ranked: [],
          review: [
            {
              asset_id: "b",
              review_reasons: ["forecast_unavailable"],
            },
          ],
        },
        calls: [],
        plan: {
          locations: [
            { asset_id: "b", reasons: ["readiness_review_required"] },
          ],
          response: null,
        },
      });

      const [row] = callRows(reviewIncident);

      assert.equal(row.followup, false);
      assert.deepEqual(row.reasons, []);
      assert.deepEqual(row.reviewReasons, [
        "Forecast unavailable",
        "Readiness review required",
      ]);
    },
  );
  await t.test(
    "readiness fallback retains early adverse facts and reports later conflicts",
    () => {
      const readinessIncident = toIncident({
        ...state,
        calls: [
          {
            request_id: "adverse",
            asset_id: "a",
            status: "completed",
            observed_at: "2026-09-20T10:00:00Z",
            can_self_evacuate: false,
            reported_needs_assistance: true,
            transport_available: false,
            provenance: { source: "stored_call_assessment" },
          },
          {
            request_id: "optimistic",
            asset_id: "a",
            status: "completed",
            observed_at: "2026-09-20T10:01:00Z",
            can_self_evacuate: true,
            reported_needs_assistance: false,
            transport_available: true,
          },
          {
            request_id: "incomplete",
            asset_id: "a",
            status: "completed",
            observed_at: "2026-09-20T10:02:00Z",
          },
        ],
        plan: { locations: [], response: null },
      });
      const row = callRows(readinessIncident).find(
        (candidate) => candidate.asset_id === "a",
      );

      assert.deepEqual(readinessFacts(readinessIncident, row), {
        canSelfEvacuate: false,
        needsAssistance: true,
        transportAvailable: false,
        departureConfirmed: null,
        arrivalConfirmed: null,
        assistanceReviewRequired: false,
        conflicts: [
          "can_self_evacuate",
          "needs_assistance",
          "transport_available",
        ],
        source: "Call history fallback",
        requestIds: ["adverse", "optimistic", "incomplete"],
      });
    },
  );
  await t.test(
    "authoritative merged plan readiness wins over raw calls",
    () => {
      const readinessIncident = toIncident({
        ...state,
        calls: [
          {
            request_id: "raw",
            asset_id: "a",
            status: "completed",
            can_self_evacuate: false,
            reported_needs_assistance: true,
            transport_available: false,
            departure_confirmed: true,
            arrival_confirmed: false,
          },
        ],
        plan: {
          locations: [
            {
              asset_id: "a",
              reported_can_self_evacuate: true,
              reported_needs_assistance: false,
              reported_transport_available: true,
              assistance_review_required: true,
              call_source: "stored_call_assessment",
              assessment_request_ids: ["merged"],
            },
          ],
          response: null,
        },
      });
      const row = callRows(readinessIncident).find(
        (candidate) => candidate.asset_id === "a",
      );
      const facts = readinessFacts(readinessIncident, row);

      assert.equal(facts.canSelfEvacuate, true);
      assert.equal(facts.needsAssistance, false);
      assert.equal(facts.transportAvailable, true);
      assert.equal(facts.departureConfirmed, true);
      assert.equal(facts.arrivalConfirmed, false);
      assert.equal(facts.assistanceReviewRequired, true);
      assert.equal(facts.source, "Stored call assessment · merged plan");
      assert.deepEqual(facts.requestIds, ["merged"]);
    },
  );
  await t.test(
    "absent readiness and a historically completed call remain unknown",
    () => {
      const readinessIncident = toIncident({
        ...state,
        calls: [
          {
            request_id: "historical",
            asset_id: "a",
            snapshot_id: "older-snapshot",
            status: "completed",
            message_acknowledged: true,
          },
        ],
        plan: { locations: [], response: null },
      });
      const row = callRows(readinessIncident).find(
        (candidate) => candidate.asset_id === "a",
      );
      const facts = readinessFacts(readinessIncident, row);

      assert.equal(facts.canSelfEvacuate, null);
      assert.equal(facts.needsAssistance, null);
      assert.equal(facts.transportAvailable, null);
      assert.equal(facts.departureConfirmed, null);
      assert.equal(facts.arrivalConfirmed, null);
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

test("GPS preserves WGS84 order and rejects missing or invalid positions", async () => {
  const { gps } = await import(path);
  assert.equal(
    gps({ latitude: 41.953, longitude: 3.022 }),
    "41.95300, 3.02200",
  );
  assert.equal(gps({ latitude: 0, longitude: 0 }), "0.00000, 0.00000");
  for (const point of [
    {},
    { latitude: null, longitude: 3 },
    { latitude: 95, longitude: 3 },
    { latitude: 42, longitude: 190 },
  ]) {
    assert.equal(gps(point), "Not supplied");
  }
});

test("call history filters attempts independently without inventing caller identity", async () => {
  const { callHistory, filterCallHistory, callActor } = await import(path);
  const rows = [
    {
      asset_id: "a",
      name: "School",
      calls: [
        {
          request_id: "one",
          caller_type: "agent",
          status: "no_answer",
          observed_at: "2026-09-19T10:00:00Z",
        },
        {
          request_id: "two",
          caller_type: "human",
          status: "completed",
          observed_at: "2026-09-20T10:00:00Z",
        },
      ],
    },
    { asset_id: "b", name: "Home", calls: [] },
  ];
  const history = callHistory(rows);
  assert.equal(history.length, 2);
  assert.equal(history[0].call.request_id, "two");
  assert.equal(
    filterCallHistory(history, { caller: "agent", outcome: "completed" })
      .length,
    0,
  );
  assert.equal(
    filterCallHistory(history, {
      caller: "human",
      from: "2026-09-20",
      search: "school",
    }).length,
    1,
  );
  assert.equal(filterCallHistory(history, { to: "2026-09-19" }).length, 1);
  assert.equal(callActor({ source: "Some agent interview" }), "unknown");
  assert.equal(callActor({}), "unknown");
});

test("crew urgency uses supplied unfinished-task timing without inventing missing deadlines", async () => {
  const { crewUrgency } = await import(path);
  assert.equal(crewUrgency([]).label, "No plan supplied");
  assert.equal(
    crewUrgency([{ status: "proposed", finish_min: 10 }]).label,
    "Timing unavailable",
  );
  assert.equal(
    crewUrgency([{ status: "completed", deadline_min: 5, finish_min: 10 }])
      .label,
    "Completed",
  );
  assert.equal(
    crewUrgency([{ deadline_min: 40, finish_min: 35 }]).tone,
    "warning",
  );
  assert.equal(
    crewUrgency([{ deadline_min: 30, finish_min: 35 }]).tone,
    "error",
  );
  const partial = crewUrgency([
    { deadline_min: 80, finish_min: 35 },
    { finish_min: 40 },
  ]);
  assert.equal(partial.partial, true);
  assert.match(partial.label, /partial timing/);
});

test("incident GPS prefers supplied point and labels a derived perimeter centre", async () => {
  const { incidentGps } = await import(path);
  assert.deepEqual(incidentGps({ latitude: 0, longitude: 0 }), {
    point: { latitude: 0, longitude: 0 },
    basis: "Incident point · lat, lon",
  });
  const perimeter = {
    type: "Polygon",
    coordinates: [
      [
        [3, 41],
        [5, 41],
        [5, 43],
        [3, 41],
      ],
    ],
  };
  assert.deepEqual(incidentGps({ fire_geometry: perimeter }), {
    point: { latitude: 42, longitude: 4 },
    basis: "Perimeter centre · lat, lon",
  });
  assert.equal(incidentGps({}).point, null);
  assert.equal(
    incidentGps({
      fire_geometry: { type: "Polygon", coordinates: [[[200, 95]]] },
    }).point,
    null,
  );
});
