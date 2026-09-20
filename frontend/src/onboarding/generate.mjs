// Illustrative dataset generated from the onboarding form. Locations, occupancy, values,
// risk, calls and response plans are synthetic: no register, no roster and no real call is involved.
import { toIncident } from "../state/model.mjs";
import {
  ACTION_CAPABILITIES,
  CATALONIA,
  RESOURCE_TYPES,
  nearestPlace,
} from "./config.mjs";

const hash = (text) => {
  let value = 0x811c9dc5;
  for (let index = 0; index < text.length; index++) {
    value ^= text.charCodeAt(index);
    value = Math.imul(value, 0x01000193) >>> 0;
  }
  return value >>> 0;
};

const generator = (seed) => {
  let state = seed >>> 0;
  const next = () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    next,
    between: (min, max) => min + next() * (max - min),
    int: (min, max) => min + Math.floor(next() * (max - min + 1)),
    pick: (items) => items[Math.floor(next() * items.length)],
    shuffle: (items) => {
      const shuffled = [...items];
      for (let index = shuffled.length - 1; index > 0; index--) {
        const swap = Math.floor(next() * (index + 1));
        [shuffled[index], shuffled[swap]] = [shuffled[swap], shuffled[index]];
      }
      return shuffled;
    },
  };
};

const METRES_PER_DEGREE = 111320;
const round = (value, places) => Number(value.toFixed(places));
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const narrow = (latitude) => Math.cos((latitude * Math.PI) / 180);

const offset = (point, northM, eastM) => ({
  latitude: round(point.latitude + northM / METRES_PER_DEGREE, 5),
  longitude: round(
    point.longitude + eastM / (METRES_PER_DEGREE * narrow(point.latitude)),
    5,
  ),
});

const distanceM = (a, b) =>
  Math.hypot(
    (a.latitude - b.latitude) * METRES_PER_DEGREE,
    (a.longitude - b.longitude) * METRES_PER_DEGREE * narrow(a.latitude),
  );

/** Jurisdiction envelope: the supplied fires and station, widened and clipped to Catalonia. */
export function territory(config) {
  const points = [...config.fires, ...(config.station ? [config.station] : [])];
  const pad = 0.12;
  return {
    latMin: Math.max(
      CATALONIA.latMin,
      Math.min(...points.map((p) => p.latitude)) - pad,
    ),
    latMax: Math.min(
      CATALONIA.latMax,
      Math.max(...points.map((p) => p.latitude)) + pad,
    ),
    lonMin: Math.max(
      CATALONIA.lonMin,
      Math.min(...points.map((p) => p.longitude)) - pad,
    ),
    lonMax: Math.min(
      CATALONIA.lonMax,
      Math.max(...points.map((p) => p.longitude)) + pad,
    ),
  };
}

const ASSET_KINDS = [
  {
    type: "care_home",
    prefix: "Residència",
    occupancy: [28, 96],
    value: [1800000, 4200000],
    tier: "critical",
    evacuation: [40, 80],
  },
  {
    type: "school",
    prefix: "Escola",
    occupancy: [60, 260],
    value: [1200000, 3400000],
    tier: "high",
    evacuation: [20, 45],
  },
  {
    type: "campsite",
    prefix: "Càmping",
    occupancy: [40, 320],
    value: [600000, 2000000],
    tier: "high",
    evacuation: [25, 60],
  },
  {
    type: "hotel",
    prefix: "Hotel",
    occupancy: [30, 180],
    value: [900000, 3000000],
    tier: "high",
    evacuation: [20, 50],
  },
  {
    type: "residential",
    prefix: "Urbanització",
    occupancy: [6, 60],
    value: [300000, 1100000],
    tier: "standard",
    evacuation: [15, 35],
  },
  {
    type: "farm",
    prefix: "Mas",
    occupancy: [2, 12],
    value: [240000, 900000],
    tier: "standard",
    evacuation: [10, 30],
  },
  {
    type: "community_centre",
    prefix: "Centre Cívic",
    occupancy: [15, 90],
    value: [500000, 1600000],
    tier: "standard",
    evacuation: [15, 35],
  },
  {
    type: "industrial",
    prefix: "Polígon",
    occupancy: [8, 70],
    value: [800000, 2800000],
    tier: "standard",
    evacuation: [20, 45],
  },
];

const TOPONYMS = [
  "del Pinar",
  "de la Roureda",
  "de Sant Martí",
  "del Puig Alt",
  "de la Vall Fosca",
  "del Coll Verd",
  "de la Riera Seca",
  "del Mas Nou",
  "de la Font Freda",
  "de la Serra Gran",
  "del Camí Ral",
  "del Pla de Dalt",
  "de les Alzines",
  "del Torrent Blau",
  "de la Solana",
];

const TIER_WEIGHT = { critical: 1, high: 0.7, standard: 0.4 };
const SOURCE = "Session onboarding demo";
const NOTE =
  "Generated from the onboarding form. Illustrative only: not a register, roster, forecast or real call.";

const minutesAfter = (iso, minutes) =>
  new Date(Date.parse(iso) + minutes * 60000).toISOString();

const slug = (text) =>
  text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "department";

function perimeter(centre, rng) {
  const radius = rng.between(900, 2600);
  const ring = [];
  for (let step = 0; step < 11; step++) {
    const angle = (step / 11) * 2 * Math.PI;
    const reach = radius * rng.between(0.66, 1.28);
    const point = offset(
      centre,
      Math.cos(angle) * reach,
      Math.sin(angle) * reach,
    );
    ring.push([point.longitude, point.latitude]);
  }
  ring.push([...ring[0]]);
  return { type: "Polygon", coordinates: [ring] };
}

function buildAssets(incidentId, centre, place, phones, rng) {
  const total = Math.max(3, phones.length);
  const kinds = [ASSET_KINDS[0], ...rng.shuffle(ASSET_KINDS.slice(1))];
  const names = rng.shuffle(TOPONYMS);
  // A single unassessed location keeps the "forecast not supplied" path visible.
  const unassessed = total >= 4 ? total - 1 : -1;
  const spreadRate = round(rng.between(18, 38), 1);
  return {
    spreadRate,
    assets: Array.from({ length: total }, (_, index) => {
      const kind = kinds[index % kinds.length];
      const bearing = rng.between(0, 2 * Math.PI);
      const reach = rng.between(420, 4600);
      const point = offset(
        centre,
        Math.cos(bearing) * reach,
        Math.sin(bearing) * reach,
      );
      const distance = Math.round(distanceM(centre, point));
      const occupancy = rng.int(kind.occupancy[0], kind.occupancy[1]);
      const value =
        Math.round(rng.between(kind.value[0], kind.value[1]) / 10000) * 10000;
      const probability =
        index === unassessed
          ? null
          : round(
              clamp(
                (0.94 - distance / 5400) * rng.between(0.78, 1.1),
                0.03,
                0.95,
              ),
              2,
            );
      const arrival = Math.round(distance / spreadRate);
      const evacuation = rng.int(kind.evacuation[0], kind.evacuation[1]);
      const loss = (fraction) =>
        probability === null
          ? null
          : Math.round((value * probability * fraction) / 1000) * 1000;
      return {
        asset_id: `${incidentId}-loc-${index + 1}`,
        name: `${kind.prefix} ${names[index % names.length]}`,
        asset_type: kind.type,
        municipality: place.name,
        latitude: point.latitude,
        longitude: point.longitude,
        distance_to_fire_m: distance,
        estimated_occupancy: occupancy,
        occupancy_basis: "Illustrative population estimate",
        replacement_value_eur: value,
        replacement_value_basis:
          "Illustrative replacement cost, not an appraisal",
        expected_loss_eur_low: loss(0.18),
        expected_loss_eur_mid: loss(0.38),
        expected_loss_eur_high: loss(0.7),
        burn_probability: probability,
        risk_score:
          probability === null
            ? null
            : clamp(
                Math.round(
                  100 *
                    (0.55 * probability +
                      0.25 * Math.min(1, occupancy / 200) +
                      0.2 * TIER_WEIGHT[kind.tier]),
                ),
                1,
                99,
              ),
        risk_label: probability === null ? "Not assessed" : null,
        criticality_tier: kind.tier,
        evacuation_min: evacuation,
        fire_arrival_min: probability === null ? null : arrival,
        contact_phone: phones[index] ?? null,
        assessed_at: null,
        sources: [
          {
            source: SOURCE,
            observed_at: null,
            fields: [
              "estimated_occupancy",
              "replacement_value_eur",
              "burn_probability",
              "risk_score",
            ],
            notes: NOTE,
          },
        ],
      };
    }),
  };
}

function buildCalls(incidentId, reachable, asOf, fireIndex) {
  // The agent works the supplied numbers in priority order; the rest stay queued.
  const attempted = Math.min(3, Math.max(1, reachable.length - 1));
  return reachable.map((asset, index) => {
    const base = {
      request_id: `${incidentId}-call-${index + 1}`,
      asset_id: asset.asset_id,
      caller_type: "agent",
      contact_phone: asset.contact_phone,
      source: SOURCE,
    };
    if (index >= attempted)
      return {
        ...base,
        status: "queued",
        queue_state: "pending",
        dispatch_state: "not_started",
        observed_at: minutesAfter(asOf, -2),
      };
    const observed_at = minutesAfter(asOf, -22 + index * 5);
    if (index === 0)
      return {
        ...base,
        status: "completed",
        observed_at,
        message_acknowledged: true,
        can_self_evacuate: false,
        reported_needs_assistance: true,
        transport_available: false,
        wants_human: true,
        human_followup_required: true,
        human_followup_reasons: ["assisted_transport_required"],
        departure_confirmed: false,
        arrival_confirmed: false,
      };
    if (index === 1)
      return {
        ...base,
        status: "completed",
        observed_at,
        message_acknowledged: true,
        can_self_evacuate: true,
        reported_needs_assistance: false,
        transport_available: true,
        departure_confirmed: true,
        arrival_confirmed: fireIndex % 2 === 1,
      };
    return {
      ...base,
      status: "no_answer",
      observed_at,
      human_followup_reasons: ["agent_interview_inconclusive"],
    };
  });
}

function buildIncident(config, fire, fireIndex, units, seed) {
  const rng = generator(hash(`${seed}:${fireIndex}`));
  const place = nearestPlace(fire);
  const incidentId = `onb-${seed.toString(36)}-${fireIndex + 1}`;
  const asOf = config.created_at;
  const phones = config.phones.filter(
    (_, index) => index % config.fires.length === fireIndex,
  );
  const { assets, spreadRate } = buildAssets(
    incidentId,
    fire,
    place,
    phones,
    rng,
  );
  for (const asset of assets) {
    asset.assessed_at = minutesAfter(asOf, -rng.int(4, 26));
    asset.sources[0].observed_at = asset.assessed_at;
    asset.risk_label =
      asset.risk_label ??
      (asset.risk_score >= 70
        ? "High"
        : asset.risk_score >= 40
          ? "Moderate"
          : "Low");
  }
  const reachable = assets.filter((asset) => asset.contact_phone);
  const calls = buildCalls(incidentId, reachable, asOf, fireIndex);
  const callFor = (asset) =>
    calls.find((call) => call.asset_id === asset.asset_id);

  const ranked = assets
    .filter((asset) => asset.contact_phone && asset.fire_arrival_min !== null)
    .sort((a, b) => b.risk_score - a.risk_score)
    .map((asset, index) => ({
      asset_id: asset.asset_id,
      rank: index + 1,
      slack_min: asset.fire_arrival_min - asset.evacuation_min,
      status:
        asset.fire_arrival_min - asset.evacuation_min <= 0
          ? "window_exhausted"
          : "window_open",
      contact_phone: asset.contact_phone,
      policy: `Illustrative arrival estimate at ${spreadRate} m/min minus the evacuation duration`,
    }));
  const review = assets
    .filter((asset) => !asset.contact_phone || asset.fire_arrival_min === null)
    .map((asset) => ({
      asset_id: asset.asset_id,
      contact_phone: asset.contact_phone,
      review_reasons: [
        ...(asset.contact_phone ? [] : ["no_contact_number"]),
        ...(asset.fire_arrival_min === null ? ["forecast_unavailable"] : []),
      ],
    }));

  const locations = assets.map((asset) => {
    const call = callFor(asset);
    const answered = call?.status === "completed";
    const assisted = call?.reported_needs_assistance === true;
    return {
      asset_id: asset.asset_id,
      mode: !answered
        ? "undetermined"
        : assisted
          ? "assisted_evacuation"
          : "self_evacuate",
      destination_name: answered ? `${place.name} reception centre` : null,
      reasons: [
        ...(assisted ? ["assisted_transport_required"] : []),
        ...(asset.contact_phone ? [] : ["no_contact_number"]),
      ],
      evacuation_status: call?.arrival_confirmed
        ? "arrived"
        : call?.departure_confirmed
          ? "departure_confirmed"
          : "not_confirmed",
      human_followup: call?.human_followup_required === true,
      call_source: answered ? "voice_agent" : undefined,
      assessment_request_ids: call ? [call.request_id] : [],
      ...(answered
        ? {
            reported_can_self_evacuate: call.can_self_evacuate === true,
            reported_needs_assistance: assisted,
            reported_transport_available: call.transport_available === true,
          }
        : {}),
    };
  });

  // A resource is only sent where its capabilities apply: a location that asked for
  // assistance needs an assisted_evacuation resource. The rest is left unserved with
  // the reason, rather than mis-tasked.
  const assists = (unit) => unit.capabilities.includes("assisted_evacuation");
  const byRisk = (a, b) => (b.risk_score ?? -1) - (a.risk_score ?? -1);
  const needsAssistance = (asset) =>
    callFor(asset)?.reported_needs_assistance === true;
  const assignments = new Map(units.map((unit) => [unit.id, []]));
  const share = (locations, pool, perUnit) => {
    if (!pool.length) return;
    locations
      .slice(0, pool.length * perUnit)
      .forEach((asset, index) =>
        assignments.get(pool[index % pool.length].id).push(asset),
      );
  };
  share(assets.filter(needsAssistance).sort(byRisk), units.filter(assists), 2);
  share(
    assets.filter((asset) => !needsAssistance(asset)).sort(byRisk),
    units.filter((unit) => !assists(unit)),
    2,
  );
  const planned = units.filter((unit) => assignments.get(unit.id).length > 0);

  const teams = planned.map((unit) => {
    let cursor = 0;
    return {
      team_id: unit.id,
      current_location: { latitude: unit.latitude, longitude: unit.longitude },
      tasks: assignments.get(unit.id).map((asset, index) => {
        const from = index === 0 ? unit : assignments.get(unit.id)[index - 1];
        const travel = Math.max(
          5,
          Math.round((distanceM(from, asset) / 1000 / 45) * 60),
        );
        const assisted = needsAssistance(asset);
        const action = assisted ? "assisted_evacuation" : unit.action;
        const duration = assisted ? asset.evacuation_min : rng.int(12, 24);
        const depart = cursor;
        const start = depart + travel;
        cursor = start + duration;
        return {
          action_id: `${incidentId}-${unit.id}-${index + 1}`,
          asset_id: asset.asset_id,
          action,
          status: "proposed",
          depart_min: depart,
          travel_min: travel,
          start_min: start,
          finish_min: cursor,
          deadline_min: asset.fire_arrival_min,
          required_capabilities: ACTION_CAPABILITIES[action],
          prerequisites: assisted
            ? ["Accessible transport confirmed", "Reception capacity confirmed"]
            : [],
          route_source: "Illustrative straight-line route",
          path_lonlat: [
            [from.longitude, from.latitude],
            [
              round(
                (from.longitude + asset.longitude) / 2 +
                  rng.between(-0.006, 0.006),
                5,
              ),
              round(
                (from.latitude + asset.latitude) / 2 +
                  rng.between(-0.006, 0.006),
                5,
              ),
            ],
            [asset.longitude, asset.latitude],
          ],
        };
      }),
    };
  });

  const served = new Set(
    teams.flatMap((team) => team.tasks.map((task) => task.asset_id)),
  );
  const unserved = Object.fromEntries(
    assets
      .filter((asset) => !served.has(asset.asset_id))
      .map((asset) => [
        asset.asset_id,
        needsAssistance(asset)
          ? "No assisted-evacuation resource free inside the evacuation window"
          : !asset.contact_phone
            ? "Contact number not supplied; no resource assigned"
            : asset.fire_arrival_min === null
              ? "Awaiting forecast before a resource is assigned"
              : "No resource free inside the evacuation window",
      ]),
  );

  const assistance = calls.filter(
    (call) => call.reported_needs_assistance === true,
  );
  const departures = calls.filter((call) => call.departure_confirmed === true);
  const events = [
    ["fire_update", `Fire point received · ${place.name}`, "info", null],
    ["assessment", `${assets.length} locations assessed`, "info", null],
    ...calls
      .filter((call) => call.status === "completed")
      .map((call) => [
        "call_completed",
        `Agent conversation completed for ${assets.find((a) => a.asset_id === call.asset_id).name}`,
        "info",
        call.asset_id,
      ]),
    ...assistance.map((call) => [
      "human_followup",
      `Assisted transport and a human callback requested by ${assets.find((a) => a.asset_id === call.asset_id).name}`,
      "warning",
      call.asset_id,
    ]),
    ...departures.map((call) => [
      "evacuation",
      `Departure reported for ${assets.find((a) => a.asset_id === call.asset_id).name}`,
      "info",
      call.asset_id,
    ]),
    [
      "plan_updated",
      `${teams.length} response plans proposed for ${config.department}`,
      "info",
      null,
    ],
    ...(calls.some((call) => call.status === "queued")
      ? [
          [
            "call_queued",
            `${calls.filter((call) => call.status === "queued").length} numbers queued for the voice agent`,
            "info",
            null,
          ],
        ]
      : []),
  ].map(([kind, notes, severity, asset_id], index, all) => ({
    event_id: `${incidentId}-event-${index + 1}`,
    ingestion_sequence: index + 1,
    as_of: minutesAfter(asOf, -(all.length - index) * 3),
    received_at: minutesAfter(asOf, -(all.length - index) * 3 + 1),
    kind,
    notes,
    severity,
    asset_id,
    source: SOURCE,
  }));

  return toIncident({
    schema_version: "coordination-state-1",
    scenario_id: incidentId,
    incident_id: incidentId,
    name:
      config.fires.length > 1
        ? `${place.name} fire ${fireIndex + 1}`
        : `${place.name} fire`,
    incident_status: "active",
    area: `Near ${place.name}, Catalonia`,
    revision: 1,
    snapshot_id: `${incidentId}-onboarding-${seed.toString(36)}`,
    as_of: asOf,
    snapshot_as_of: asOf,
    input_mode: "session_onboarding",
    latitude: fire.latitude,
    longitude: fire.longitude,
    jurisdiction: config.department,
    spread_rate_m_per_min: spreadRate,
    assets,
    contacts: { ranked, review },
    calls,
    resources: units.map((unit) => ({
      id: unit.id,
      team_id: unit.id,
      name: unit.name,
      type: unit.label,
      capabilities: unit.capabilities,
      status: assignments.get(unit.id)?.length ? "deployed" : "standby",
      available: !assignments.get(unit.id)?.length,
      latitude: unit.latitude,
      longitude: unit.longitude,
      home_station: config.department,
      placement_basis: unit.placementBasis,
      source: `${config.department} · ${SOURCE}`,
      updated_at: asOf,
    })),
    teams: units.map((unit) => ({
      team_id: unit.id,
      name: unit.name,
      capabilities: unit.capabilities,
      available: !assignments.get(unit.id)?.length,
      source: `${config.department} · ${SOURCE}`,
    })),
    peopleClusters: assets.map((asset) => {
      const call = callFor(asset);
      return {
        id: `${asset.asset_id}-group`,
        asset_id: asset.asset_id,
        name: asset.name,
        people: asset.estimated_occupancy,
        status: call?.arrival_confirmed
          ? "arrived"
          : call?.reported_needs_assistance === true
            ? "needs_assistance"
            : call?.departure_confirmed
              ? "self_evacuating"
              : "unknown",
        source: SOURCE,
        updated_at: asOf,
      };
    }),
    events,
    fire_geometry: perimeter(fire, rng),
    plan: {
      locations,
      response: {
        schema_version: "multi-response-plan-1",
        dispatch: false,
        teams,
        unserved,
        assumptions: [
          NOTE,
          "Proposed visits are not dispatch instructions.",
          `Deadlines use an illustrative ${spreadRate} m/min spread from the supplied fire point.`,
        ],
      },
    },
  });
}

function buildUnits(config, seed) {
  const rng = generator(hash(`${seed}:resources`));
  const box = territory(config);
  const prefix = slug(config.department);
  const units = [];
  for (const resource of config.resources) {
    const type = RESOURCE_TYPES.find((entry) => entry.id === resource.type);
    for (let index = 0; index < resource.count; index++) {
      const point =
        resource.placement === "station"
          ? offset(
              config.station,
              rng.between(-260, 260),
              rng.between(-260, 260),
            )
          : {
              latitude: round(rng.between(box.latMin, box.latMax), 5),
              longitude: round(rng.between(box.lonMin, box.lonMax), 5),
            };
      units.push({
        id: `${prefix}-${type.id}-${index + 1}`,
        name: `${type.label} ${index + 1}`,
        label: type.label,
        capabilities: type.capabilities,
        action: type.action,
        placementBasis:
          resource.placement === "station"
            ? "Placed at the supplied fire station"
            : "Randomly placed inside the jurisdiction envelope",
        ...point,
      });
    }
  }
  return units;
}

/** Deterministic: the same configuration always produces the same dashboard. */
export function onboardingIncidents(config) {
  const seed = hash(
    JSON.stringify([
      config.department,
      config.created_at,
      config.fires,
      config.phones,
      config.resources,
      config.station,
    ]),
  );
  const units = buildUnits(config, seed);
  return config.fires.map((fire, index) =>
    buildIncident(
      config,
      fire,
      index,
      units.filter((_, unitIndex) => unitIndex % config.fires.length === index),
      seed,
    ),
  );
}
