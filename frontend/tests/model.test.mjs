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
    "41.95, 3.02",
  );
  assert.equal(gps({ latitude: 0, longitude: 0 }), "0.00, 0.00");
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

test("GPS centre rejects malformed/open polygon rings and accepts MultiPolygon", async () => {
  const { incidentGps } = await import(path);
  for (const coordinates of [
    [2, 41],
    [[[2, 41]]],
    [
      [
        [2, 41],
        [3, 42],
        [3, 41],
        [4, 41],
      ],
    ],
  ]) {
    assert.equal(
      incidentGps({ fire_geometry: { type: "Polygon", coordinates } }).point,
      null,
    );
  }
  assert.deepEqual(
    incidentGps({
      fire_geometry: {
        type: "MultiPolygon",
        coordinates: [
          [
            [
              [2, 40],
              [4, 40],
              [4, 42],
              [2, 40],
            ],
          ],
        ],
      },
    }).point,
    { latitude: 41, longitude: 3 },
  );
});

test('terminal no-answer attempts are attempted, not completed or waiting to call', async () => {
  const { toIncident, callRows } = await import(path);
  const incident = toIncident({
    assets: ['a', 'b', 'c'].map(asset_id => ({ asset_id })),
    calls: ['a', 'b', 'c'].map(asset_id => ({ asset_id, status: 'no_answer', dispatch_state: 'bound', human_followup_required: true })),
  });
  const rows = callRows(incident);
  assert.equal(rows.filter(row => row.toCall).length, 0);
  assert.equal(rows.filter(row => row.attempted).length, 3);
  assert.equal(rows.filter(row => row.completed).length, 0);
  assert.equal(rows.filter(row => row.followup).length, 3);
});

test('call state distinguishes uncalled, queued retries, active attempts, completed and unknown records', async () => {
  const { toIncident, callRows } = await import(path);
  const calls = [
    { asset_id: 'queued', status: 'queued', dispatch_state: 'not_started' },
    { asset_id: 'retry', status: 'no_answer', dispatch_state: 'bound' },
    { asset_id: 'retry', status: 'queued', dispatch_state: 'not_started' },
    { asset_id: 'active', status: 'ringing', dispatch_state: 'bound' },
    { asset_id: 'uncertain', status: 'queued', dispatch_state: 'outcome_unknown' },
    { asset_id: 'done', status: 'completed' },
    { asset_id: 'unknown', status: null },
    ...['failed', 'declined', 'busy'].map(status => ({ asset_id: status, status })),
  ];
  const rows = callRows(toIncident({
    assets: [...new Set(['uncalled', ...calls.map(c => c.asset_id)])].map(asset_id => ({asset_id})), calls,
  }));
  const ids = field => rows.filter(row => row[field]).map(row => row.asset_id);
  assert.deepEqual(ids('uncalled'), ['uncalled']);
  assert.deepEqual(ids('queued'), ['queued', 'retry']);
  assert.deepEqual(ids('toCall'), ['uncalled', 'queued', 'retry']);
  assert.deepEqual(ids('attempted'), ['retry', 'active', 'uncertain', 'done', 'failed', 'declined', 'busy']);
  assert.deepEqual(ids('completed'), ['done']);
});

test('queue membership excludes cancelled, held and started requests despite queued call status', async () => {
  const { toIncident, callRows } = await import(path);
  const states = ['pending', 'review', 'started', 'cancelled', null, undefined];
  const rows = callRows(toIncident({
    assets: states.map((_, index) => ({asset_id: String(index)})),
    calls: states.map((queue_state, index) => ({
      asset_id: String(index), status: 'queued', dispatch_state: 'not_started', queue_state,
    })),
  }));
  assert.deepEqual(rows.map(row => row.toCall), [true, false, false, false, true, true]);
  assert.deepEqual(rows.map(row => row.uncalled), [false, false, false, false, false, false]);
  const terminal = callRows(toIncident({
    assets: [{asset_id: 'a'}],
    calls: [{asset_id: 'a', status: 'no_answer', dispatch_state: 'bound', queue_state: 'pending'}],
  }))[0];
  assert.equal(terminal.toCall, false, 'stale pending membership must not requeue a terminal attempt');
});

test('distance to fire formats supplied metres without inventing missing distances', async () => {
  const { distanceToFire } = await import(path);
  assert.equal(distanceToFire(0), '0 m');
  assert.equal(distanceToFire(425), '425 m');
  assert.equal(distanceToFire(1250), '1.25 km');
  for (const value of [null, undefined, -1, NaN, '1250']) assert.equal(distanceToFire(value), 'Not supplied');
});

test('local date display and filters agree across the UTC day boundary', async () => {
  const {stamp, withinDate} = await import(path);
  const previous = process.env.TZ;
  process.env.TZ = 'America/Los_Angeles';
  try {
    assert.equal(withinDate('2026-09-20T00:30:00Z', '2026-09-19', '2026-09-19'), true);
    assert.equal(withinDate('2026-09-20T00:30:00Z', '2026-09-20', ''), false);
    assert.equal(stamp('2026-09-20T00:30:00Z'), new Intl.DateTimeFormat(undefined, {year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',timeZoneName:'short'}).format(new Date('2026-09-20T00:30:00Z')));
    assert.equal(stamp(null), 'Not supplied');
  } finally { if(previous === undefined) delete process.env.TZ; else process.env.TZ=previous; }
});

test('generated historical fixtures do not expose future observations or final call-derived plans', () => {
  const history = JSON.parse(fs.readFileSync(new URL('../fixtures/design-history.json', import.meta.url), 'utf8'));
  assert.equal(history.generated, true);
  assert.equal(history.entries.length, 3);
  for(const entry of history.entries) for(const incident of entry.incidents) {
    assert.equal(incident.calls.length, 0);
    assert.equal(incident.plan.response, null);
    for(const asset of incident.assets) for(const source of asset.sources || []) assert.ok(Date.parse(source.observed_at) <= Date.parse(entry.as_of));
    for(const location of incident.plan.locations) {assert.deepEqual(location.reasons, []); assert.equal(location.destination_name, null);}
  }
});


test("displayed decimals use at most two places without changing numeric evidence", async () => {
  const { count, money, percentage, number, gps } = await import("../src/state/model.mjs");
  const value = 1234.56789;
  assert.equal(count(value), "1,234.57");
  assert.equal(count(12), "12");
  assert.equal(money(value), "€1,234.57");
  assert.equal(percentage(0.123456), "12.35%");
  assert.equal(gps({latitude: 41.953456, longitude: 3.022345}), "41.95, 3.02");
  assert.equal(number(value), value);
  assert.equal(count(null), "—");
  assert.equal(percentage(null), "—");
});
