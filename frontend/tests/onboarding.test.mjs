import test from "node:test";
import assert from "node:assert/strict";
import {
  CATALONIA,
  CREW_TYPES,
  buildConfig,
  defaultDraft,
  draftFromConfig,
  isConfig,
  nearestPlace,
  parseCoordinates,
  parsePhones,
  summarise,
} from "../src/onboarding/config.mjs";
import { onboardingIncidents, territory } from "../src/onboarding/generate.mjs";
import {
  callRows,
  metrics,
  evacuationTotals,
  responseTeams,
} from "../src/state/model.mjs";

const AT = "2026-09-20T10:30:00.000Z";
// A complete draft the tests own, so they do not depend on what the form pre-fills.
const COMPLETE = {
  department: "Bombers de la Bisbal",
  fires: "41.9400, 3.0400\n41.8600, 2.9100",
  station: "41.9600, 3.0380",
  phones: "+34600111222\n+34600333444\n+34600555666\n+34600777888",
  crews: {
    ...defaultDraft().crews,
    ground_crew: { count: 3, placement: "station" },
    fire_engine: { count: 2, placement: "station" },
    evacuation_bus: { count: 1, placement: "station" },
  },
};
const draft = (overrides) => ({ ...COMPLETE, ...overrides });
const config = (overrides) =>
  buildConfig(draft(overrides), { createdAt: AT }).config;

test("the form starts with no call list and no crews, and says so", () => {
  const { config: bare, problems } = buildConfig(defaultDraft(), {
    createdAt: AT,
  });
  assert.equal(bare.phones.length, 0);
  assert.equal(bare.crews.length, 0);
  assert.deepEqual(problems, [
    {
      step: "calls",
      message: "Supply at least one phone number for the voice agent to call.",
    },
    {
      step: "crews",
      message: "Set a crew count above zero for at least one type.",
    },
  ]);
  assert.equal(isConfig(bare), false);
  // One number is enough to satisfy the call list.
  assert.deepEqual(
    buildConfig(
      {
        ...defaultDraft(),
        phones: "+34600111222",
        crews: {
          ...defaultDraft().crews,
          ground_crew: { count: 1, placement: "station" },
        },
      },
      { createdAt: AT },
    ).problems,
    [],
  );
});

test("a GPS pair per line inside Catalonia is one fire", () => {
  const { points, problems } = parseCoordinates(
    [
      "41.9400, 3.0400",
      "  41.86 2.91  ",
      "",
      "# a comment",
      "48.85, 2.35",
      "not coordinates",
    ].join("\n"),
  );
  assert.deepEqual(
    points.map((p) => [p.latitude, p.longitude]),
    [
      [41.94, 3.04],
      [41.86, 2.91],
    ],
  );
  assert.deepEqual(
    problems.map((p) => p.reason),
    [`Outside ${CATALONIA.label}`, "Not a latitude, longitude pair"],
  );
});

test("the call list keeps dialable numbers once each", () => {
  const { numbers, problems } = parsePhones(
    ["+34 600 111 222", "+34600111222", "(972) 123-456", "call the mayor"].join(
      "\n",
    ),
  );
  assert.deepEqual(
    numbers.map((entry) => entry.number),
    ["+34600111222", "972123456"],
  );
  assert.deepEqual(
    problems.map((problem) => problem.reason),
    ["Duplicate number", "Not a dialable number"],
  );
});

test("an incomplete configuration reports what is missing and is not storable", () => {
  const { config: partial, problems } = buildConfig(
    {
      department: "",
      fires: "48.85, 2.35",
      station: "",
      phones: "nope",
      crews: {},
    },
    { createdAt: AT },
  );
  assert.equal(problems.length, 4);
  assert.equal(isConfig(partial), false);
});

test("station coordinates are required only when a crew type starts there", () => {
  const roaming = {
    ...COMPLETE.crews,
    ground_crew: { count: 2, placement: "territory" },
    fire_engine: { count: 0, placement: "territory" },
    evacuation_bus: { count: 0, placement: "station" },
  };
  const away = buildConfig(draft({ station: "", crews: roaming }), {
    createdAt: AT,
  });
  assert.deepEqual(away.problems, []);
  assert.equal(isConfig(away.config), true);
  const parked = buildConfig(
    draft({
      station: "",
      crews: { ...roaming, ground_crew: { count: 2, placement: "station" } },
    }),
    { createdAt: AT },
  );
  assert.equal(parked.problems.length, 1);
  assert.equal(isConfig(parked.config), false);
});

test("a stored configuration reopens as the form that produced it", () => {
  const stored = config();
  assert.equal(isConfig(stored), true);
  const reopened = buildConfig(draftFromConfig(stored), { createdAt: AT });
  assert.deepEqual(reopened.problems, []);
  assert.deepEqual(reopened.config, stored);
});

test("each supplied pair becomes one incident at that point", () => {
  const supplied = config({
    fires: "41.9400, 3.0400\n41.8600, 2.9100\n42.1000, 2.5000",
  });
  const incidents = onboardingIncidents(supplied);
  assert.equal(incidents.length, 3);
  assert.deepEqual(
    incidents.map((incident) => [incident.latitude, incident.longitude]),
    supplied.fires.map((fire) => [fire.latitude, fire.longitude]),
  );
  for (const [index, incident] of incidents.entries()) {
    assert.equal(incident.schema_version, "coordination-state-1");
    assert.equal(incident.revision, 1);
    assert.equal(incident.input_mode, "session_onboarding");
    assert.ok(Array.isArray(incident.assets) && incident.assets.length >= 3);
    assert.ok(Array.isArray(incident.contacts.ranked));
    assert.ok(Array.isArray(incident.contacts.review));
    assert.equal(
      incident.area,
      `Near ${nearestPlace(supplied.fires[index]).name}, Catalonia`,
    );
    assert.equal(incident.fire_geometry.type, "Polygon");
    const ring = incident.fire_geometry.coordinates[0];
    assert.deepEqual(ring.at(0), ring.at(-1));
  }
});

test("the same configuration always generates the same dashboard", () => {
  const supplied = config();
  assert.deepEqual(
    onboardingIncidents(supplied),
    onboardingIncidents(supplied),
  );
  const moved = onboardingIncidents({
    ...supplied,
    department: "Bombers de Ripoll",
  });
  assert.notDeepEqual(moved, onboardingIncidents(supplied));
});

test("every supplied number reaches exactly one location, spread across the fires", () => {
  const supplied = config({
    fires: "41.9400, 3.0400\n41.8600, 2.9100",
    phones: [
      "+34600000001",
      "+34600000002",
      "+34600000003",
      "+34600000004",
      "+34600000005",
    ].join("\n"),
  });
  const incidents = onboardingIncidents(supplied);
  const dialled = incidents.flatMap((incident) =>
    incident.assets.map((asset) => asset.contact_phone).filter(Boolean),
  );
  assert.deepEqual([...dialled].sort(), [...supplied.phones].sort());
  assert.deepEqual(
    incidents.map(
      (incident) =>
        incident.assets.filter((asset) => asset.contact_phone).length,
    ),
    [3, 2],
  );
  for (const incident of incidents) {
    // Every call belongs to a location that has a number on file.
    for (const call of incident.calls)
      assert.ok(
        incident.assets.some(
          (asset) =>
            asset.asset_id === call.asset_id &&
            asset.contact_phone === call.contact_phone,
        ),
      );
    // Locations without a number are flagged rather than silently dropped.
    for (const asset of incident.assets.filter((a) => !a.contact_phone))
      assert.ok(
        incident.contacts.review
          .find((entry) => entry.asset_id === asset.asset_id)
          ?.review_reasons.includes("no_contact_number"),
      );
  }
});

test("the crew roster matches the requested counts per type", () => {
  const supplied = config({
    fires: "41.9400, 3.0400\n41.8600, 2.9100",
    crews: {
      ...COMPLETE.crews,
      ground_crew: { count: 3, placement: "station" },
      helicopter: { count: 2, placement: "territory" },
      fire_engine: { count: 0, placement: "station" },
      evacuation_bus: { count: 0, placement: "station" },
    },
  });
  const incidents = onboardingIncidents(supplied);
  const crews = incidents.flatMap((incident) => incident.resources);
  assert.equal(crews.length, 5);
  assert.equal(new Set(crews.map((crew) => crew.id)).size, 5);
  assert.equal(summarise(supplied).crews, 5);
  const byType = (label) => crews.filter((crew) => crew.type === label);
  assert.equal(byType("Ground crew").length, 3);
  assert.equal(byType("Helicopter").length, 2);
  for (const incident of incidents)
    assert.deepEqual(
      incident.teams.map((team) => team.team_id),
      incident.resources.map((crew) => crew.id),
    );
});

test("crews start at the station or inside the jurisdiction envelope, as chosen", () => {
  const supplied = config({
    station: "41.9600, 3.0380",
    crews: {
      ...COMPLETE.crews,
      ground_crew: { count: 4, placement: "station" },
      fire_engine: { count: 4, placement: "territory" },
      evacuation_bus: { count: 0, placement: "station" },
    },
  });
  const box = territory(supplied);
  const crews = onboardingIncidents(supplied).flatMap(
    (incident) => incident.resources,
  );
  for (const crew of crews) {
    assert.ok(
      Number.isFinite(crew.latitude) && Number.isFinite(crew.longitude),
    );
    assert.ok(crew.latitude >= box.latMin && crew.latitude <= box.latMax);
    assert.ok(crew.longitude >= box.lonMin && crew.longitude <= box.lonMax);
    const atStation = crew.type === "Ground crew";
    const metres = Math.hypot(
      (crew.latitude - supplied.station.latitude) * 111320,
      (crew.longitude - supplied.station.longitude) *
        111320 *
        Math.cos((41.96 * Math.PI) / 180),
    );
    assert.equal(metres < 500, atStation, `${crew.name} placement`);
    assert.equal(
      crew.placement_basis,
      atStation
        ? "Placed at the supplied fire station"
        : "Randomly placed inside the jurisdiction envelope",
    );
  }
});

test("generated state drives the dashboard views it is built for", () => {
  const supplied = config({
    fires: "41.9400, 3.0400",
    phones: Array.from({ length: 6 }, (_, n) => `+3460000000${n}`).join("\n"),
  });
  const incidents = onboardingIncidents(supplied);
  const rows = callRows(incidents[0]);
  assert.equal(rows.length, incidents[0].assets.length);
  assert.ok(rows.some((row) => row.toCall));
  assert.ok(rows.some((row) => row.completed));
  assert.ok(rows.some((row) => row.followup));
  const totals = metrics(incidents);
  assert.equal(totals.structures, incidents[0].assets.length);
  assert.equal(totals.clusters, incidents[0].assets.length);
  assert.ok(totals.deployed > 0);
  const evacuation = evacuationTotals(incidents[0]);
  assert.ok(evacuation.assistance > 0);
  for (const team of responseTeams(incidents[0]))
    for (const task of team.tasks) {
      assert.equal(task.status, "proposed");
      assert.ok(task.finish_min > task.start_min);
      assert.ok(
        incidents[0].assets.some((asset) => asset.asset_id === task.asset_id),
      );
    }
  // Every crew type the form offers can be generated.
  for (const type of CREW_TYPES) {
    const single = config({
      crews: {
        ...COMPLETE.crews,
        ...Object.fromEntries(
          CREW_TYPES.map((entry) => [
            entry.id,
            { count: entry.id === type.id ? 1 : 0, placement: "station" },
          ]),
        ),
      },
    });
    assert.equal(
      onboardingIncidents(single).flatMap((i) => i.resources).length,
      1,
    );
  }
});
