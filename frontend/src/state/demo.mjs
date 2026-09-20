import { toIncident } from "./model.mjs";
// Entire dataset is illustrative. Design demo is the default; ?demo=0 selects the backend.
const specs = [
  {
    id: "gavarres",
    name: "Gavarres forest fire",
    area: "Baix Empordà, Girona",
    centre: [41.94, 3.04],
    people: [78, 140, 32, 16],
    names: [
      "Vall Repòs care home",
      "Escola de la Bisbal",
      "Mas Gavarres",
      "Can Puig",
    ],
    types: ["care_home", "school", "residential", "farm"],
    resources: 3,
  },
  {
    id: "cap-creus",
    name: "Cap de Creus fire",
    area: "Alt Empordà, Girona",
    centre: [42.32, 3.2],
    people: [24, 64, 12, 8],
    names: [
      "Càmping Port de la Selva",
      "Centre Cívic de Roses",
      "Mas Ventós",
      "Sant Pere community",
    ],
    types: ["campsite", "community_centre", "farm", "residential"],
    resources: 2,
  },
  {
    id: "montseny",
    name: "Montseny hillside fire",
    area: "Vallès Oriental, Barcelona",
    centre: [41.77, 2.4],
    people: [45, 26, 10, 18],
    names: [
      "Can Montseny residence",
      "Escola del Bosc",
      "Mas Sant Roc",
      "La Costa homes",
    ],
    types: ["care_home", "school", "farm", "residential"],
    resources: 2,
  },
];
let sequence = 0;
export const demoIncidents = specs.map((s, index) => {
  const as_of = "2026-09-20T10:30:00Z";
  const assets = s.names.map((name, n) => ({
    asset_id: `${s.id}-${n}`,
    name,
    asset_type: s.types[n],
    municipality: s.area,
    latitude: s.centre[0] + [0.013, -0.014, 0.004, -0.022][n],
    longitude: s.centre[1] + [-0.018, 0.025, 0.04, -0.028][n],
    estimated_occupancy: s.people[n],
    distance_to_fire_m: [420, 860, 1280, null][n],
    occupancy_basis: "Illustrative population estimate",
    replacement_value_eur: [3200000, 2100000, 480000, 720000][n],
    replacement_value_basis: "Illustrative replacement cost, not an appraisal",
    expected_loss_eur_low: [480000, 210000, 24000, 36000][n],
    expected_loss_eur_mid: [960000, 420000, 96000, 72000][n],
    expected_loss_eur_high: [1600000, 840000, 192000, 144000][n],
    burn_probability: [0.62, 0.38, 0.25, null][n],
    risk_score: [82, 64, 43, null][n],
    risk_label: ["High", "High", "Moderate", "Not assessed"][n],
    criticality_tier: ["critical", "high", "standard", "standard"][n],
    evacuation_min: [45, 25, 15, 20][n],
    assessed_at: n === 3 ? "2026-09-19T18:20:00Z" : as_of,
    sources: [
      {
        source: "Design demonstration",
        observed_at: as_of,
        fields: ["estimated_occupancy", "replacement_value_eur", "risk_score"],
        notes: "Synthetic scenario. No real calls, deployments or evacuation.",
      },
    ],
  }));
  const contacts = {
    ranked: assets.slice(0, 3).map((a, n) => ({
      asset_id: a.asset_id,
      rank: n + 1,
      slack_min: [12, 28, 54][n],
      status: "window_open",
      policy: "Illustrative remaining evacuation window",
    })),
    review: [
      {
        asset_id: assets[3].asset_id,
        review_reasons: ["forecast_unavailable"],
      },
    ],
  };
  const calls = [
    {
      request_id: `${s.id}-call-1`,
      caller_type: "agent",
      asset_id: assets[0].asset_id,
      status: "completed",
      wants_human: true,
      reported_needs_assistance: true,
      human_followup_reasons: ["assisted_transport_required"],
      message_acknowledged: true,
      departure_confirmed: false,
      arrival_confirmed: false,
      observed_at: "2026-09-20T10:18:00Z",
      source: "Design demonstration",
    },
    {
      request_id: `${s.id}-call-2`,
      caller_type: "human",
      asset_id: assets[1].asset_id,
      status: "completed",
      can_self_evacuate: true,
      transport_available: true,
      message_acknowledged: true,
      departure_confirmed: true,
      arrival_confirmed: false,
      observed_at: "2026-09-20T10:22:00Z",
      source: "Design demonstration",
    },
    {
      request_id: `${s.id}-call-3`,
      caller_type: "agent",
      asset_id: assets[2].asset_id,
      status: "no_answer",
      human_followup_reasons: ["agent_interview_inconclusive"],
      observed_at: "2026-09-20T10:25:00Z",
      source: "Design demonstration",
    },
  ];
  const resources = Array.from({ length: s.resources }, (_, n) => ({
    id: `${s.id}-unit-${n}`,
    name: [`Crew ${index + 1}A`, `Engine ${index + 1}2`, `Air ${index + 1}`][n],
    type: ["Ground crew", "Fire engine", "Aircraft"][n],
    status: "deployed",
    source: "Design demonstration",
    updated_at: as_of,
  }));
  const peopleClusters = assets.map((a, n) => ({
    id: `${a.asset_id}-group`,
    asset_id: a.asset_id,
    name: a.name,
    people: a.estimated_occupancy,
    status: ["needs_assistance", "self_evacuating", "unknown", "arrived"][n],
    source: "Design demonstration",
    updated_at: as_of,
  }));
  const events = [
    ["fire_update", "Fire perimeter received", "info"],
    ["assessment", "Four locations assessed", "info"],
    [
      "call_completed",
      `Agent conversation completed for ${assets[0].name}`,
      "info",
    ],
    [
      "human_followup",
      "Assisted transport and a human callback requested",
      "warning",
    ],
    ["plan_updated", "Crew response proposal updated", "info"],
    ["evacuation", `Departure reported for ${assets[1].name}`, "info"],
  ].map(([kind, notes, severity], n) => ({
    event_id: `${s.id}-event-${n}`,
    ingestion_sequence: ++sequence,
    received_at: `2026-09-20T10:${String(10 + index * 6 + n).padStart(2, "0")}:00Z`,
    as_of: `2026-09-20T10:${String(9 + index * 6 + n).padStart(2, "0")}:00Z`,
    kind,
    notes,
    severity,
    source: "Design demonstration",
    asset_id: n > 1 ? assets[n === 5 ? 1 : 0].asset_id : null,
  }));
  return toIncident({
    schema_version: "coordination-state-1",
    scenario_id: s.id,
    incident_id: s.id,
    name: s.name,
    incident_status: "active",
    area: s.area,
    revision: 1,
    snapshot_id: `${s.id}-snapshot-${typeof __DESIGN_DEMO_VERSION__ === "undefined" ? "source" : __DESIGN_DEMO_VERSION__}`,
    as_of,
    input_mode: "offline_demo",
    assets,
    contacts,
    calls,
    resources,
    peopleClusters,
    events,
    teams: resources.map((r) => ({
      team_id: r.id,
      name: r.name,
      available: false,
    })),
    fire_geometry: {
      type: "Polygon",
      coordinates: [
        [
          [s.centre[1] - 0.02, s.centre[0] + 0.026],
          [s.centre[1] + 0.018, s.centre[0] + 0.037],
          [s.centre[1] + 0.041, s.centre[0] + 0.018],
          [s.centre[1] + 0.008, s.centre[0] + 0.001],
          [s.centre[1] - 0.02, s.centre[0] + 0.026],
        ],
      ],
    },
    plan: {
      locations: assets.map((a, n) => ({
        asset_id: a.asset_id,
        mode:
          n === 0
            ? "assisted_evacuation"
            : n === 1
              ? "self_evacuate"
              : "undetermined",
        destination_name: n < 2 ? "Municipal reception centre" : null,
        reasons: n === 0 ? ["assisted_transport_required"] : [],
        evacuation_status: n === 1 ? "departure_confirmed" : "not_confirmed",
      })),
      response: {
        schema_version: "multi-response-plan-1",
        dispatch: false,
        teams: resources
          .slice(0, 2)
          .map((r, n) => ({
            team_id: r.id,
            tasks: [
              {
                action_id: `${s.id}-assist-${n}`,
                asset_id: assets[n].asset_id,
                status: "proposed",
                depart_min: 0,
                travel_min: 8 + n * 3,
                start_min: 8 + n * 3,
                finish_min: 35 + n * 5,
                deadline_min: 35 + n * 5 + [5, 12, 30][index] + n * 8,
                prerequisites: n
                  ? ["Reception capacity confirmed"]
                  : ["Accessible transport confirmed"],
                route_source: "Illustrative route",
                path_lonlat: [
                  [s.centre[1] - 0.035, s.centre[0] - 0.03],
                  [s.centre[1] - 0.01, s.centre[0] - 0.01],
                  [assets[n].longitude, assets[n].latitude],
                ],
              },
              {
                action_id: `${s.id}-check-${n}`,
                asset_id: assets[n + 2].asset_id,
                status: "proposed",
                depart_min: 35 + n * 5,
                start_min: 43 + n * 5,
                finish_min: 55 + n * 5,
                deadline_min: 90 + n * 5,
                prerequisites: [],
                route_source: null,
              },
            ],
          }))
          .map((team) => {
            const start = {
              node_id: `${team.team_id}-start`,
              name: "Illustrative crew staging point",
              latitude: s.centre[0] - 0.03,
              longitude: s.centre[1] - 0.035,
              available_min: 0,
              source: "Design demonstration · planned departure point",
            };
            const [first, second] = team.tasks;
            const firstAsset = assets.find(
              (a) => a.asset_id === first.asset_id,
            );
            const secondAsset = assets.find(
              (a) => a.asset_id === second.asset_id,
            );
            const position = (point) => [point.longitude, point.latitude];
            const leg = (from, to, minutes, path) => ({
              from_node: from,
              to_node: to,
              travel_min: minutes,
              path_lonlat: path,
              route_source: "Illustrative route · synthetic demonstration only",
              route_status: "qualified",
              confirmed: true,
              safe: true,
              available_until_min: 180,
            });
            const forward = leg(first.asset_id, second.asset_id, 8, [
              position(firstAsset),
              position(secondAsset),
            ]);
            const routes = [
              leg(
                start.node_id,
                first.asset_id,
                first.travel_min,
                first.path_lonlat,
              ),
              forward,
              leg(start.node_id, second.asset_id, 4, [
                position(start),
                position(secondAsset),
              ]),
              leg(second.asset_id, first.asset_id, 4, [
                position(secondAsset),
                position(firstAsset),
              ]),
            ];
            const prerequisites = [
              ...new Set(team.tasks.flatMap((task) => task.prerequisites)),
            ].map((action_id) => ({
              action_id,
              status: "completed",
              finish_min: 0,
            }));
            return {
              ...team,
              starting_location: start,
              planning_context: {
                start,
                now_min: 0,
                buffer_min: 0,
                horizon_min: 180,
                available_until_min: 180,
                available: true,
                transport_capacity: 200,
                capabilities: ["assessment"],
                prerequisites,
                routes,
              },
              tasks: team.tasks.map((task, index) => ({
                ...task,
                action: index
                  ? "Assess location and report needs"
                  : "Assist occupants and verify transport",
                from_node: index ? first.asset_id : start.node_id,
                to_node: task.asset_id,
                duration_min: task.finish_min - task.start_min,
                travel_min: index ? 8 : task.travel_min,
                path_lonlat: index ? forward.path_lonlat : task.path_lonlat,
                route_source:
                  "Illustrative route · synthetic demonstration only",
                required_capabilities: ["assessment"],
                readiness_required: false,
                transport_people: 0,
                ordering_reason: index
                  ? "Illustrative secondary assessment after the earlier deadline; not an optimizer explanation."
                  : "Illustrative scenario places this earlier-deadline visit first; not a live optimizer decision.",
              })),
            };
          }),
        unserved: {
          [assets[3].asset_id]: "Awaiting forecast and occupancy confirmation",
        },
        assumptions: [
          "Illustrative plan. Proposed visits are not dispatch instructions.",
        ],
      },
    },
  });
});
