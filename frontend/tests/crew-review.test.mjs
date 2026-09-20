import test from "node:test";
import assert from "node:assert/strict";
import * as review from "../src/state/crew-review.mjs";

const tasks = [
  {
    action_id: "fixed",
    status: "started",
    finish_min: 8,
    path_lonlat: [
      [2, 41],
      [3, 42],
    ],
  },
  {
    action_id: "a",
    asset_id: "a",
    status: "proposed",
    depart_min: 8,
    travel_min: 3,
    start_min: 11,
    finish_min: 15,
    deadline_min: 30,
    path_lonlat: [
      [3, 42],
      [4, 43],
    ],
  },
  {
    action_id: "b",
    asset_id: "b",
    status: "proposed",
    depart_min: 15,
    travel_min: 5,
    start_min: 20,
    finish_min: 25,
    deadline_min: 40,
  },
  { action_id: "committed", status: "committed" },
];

test("only adjacent proposed tasks can swap without moving committed work", () => {
  assert.equal(typeof review.moveCrewStop, "function");
  assert.deepEqual(
    review.moveCrewStop(tasks, 1, 1).map((t) => t.action_id),
    ["fixed", "b", "a", "committed"],
  );
  for (const [index, delta] of [
    [0, 1],
    [1, -1],
    [2, 1],
    [3, -1],
    [1, 2],
    [3, 1],
  ]) {
    assert.deepEqual(review.moveCrewStop(tasks, index, delta), tasks);
  }
  assert.equal(tasks[1].action_id, "a");
});

test("draft order suppresses predecessor-dependent routes and times while preserving fixed work and deadlines", () => {
  assert.equal(typeof review.draftCrewTasks, "function");
  const draft = review.draftCrewTasks(review.moveCrewStop(tasks, 1, 1));
  assert.deepEqual(draft[0], tasks[0]);
  for (const task of draft.slice(1, 3)) {
    for (const key of [
      "path_lonlat",
      "depart_min",
      "travel_min",
      "start_min",
      "finish_min",
    ])
      assert.equal(task[key], undefined);
  }
  assert.equal(draft[1].deadline_min, 40);
  assert.equal(tasks[1].travel_min, 3);
});

test("review facts keep transport, self evacuation and mobility separate with unknown evidence explicit", () => {
  assert.equal(typeof review.crewStopFacts, "function");
  const incident = {
    assets: [
      { asset_id: "a", estimated_occupancy: 0, replacement_value_eur: 0 },
    ],
    plan: { locations: [] },
    calls: [
      {
        asset_id: "a",
        reported_needs_assistance: true,
        transport_available: false,
      },
    ],
  };
  const facts = review.crewStopFacts(incident, tasks[1]);
  assert.equal(facts.selfEvacuation, "Unknown");
  assert.equal(facts.mobility, "Unknown");
  assert.equal(facts.assistance, "Yes");
  assert.equal(facts.transport, "No");
  assert.equal(facts.people, "0");
  assert.equal(facts.valuation, "€0");
  assert.equal(facts.orderReason, "Unknown");
  assert.equal(facts.risk, "Unknown");
  assert.equal(facts.action, "Unknown");
});

test("only supplied ordering rationale is used; merged unknown facts remain unknown over older calls", () => {
  assert.equal(typeof review.crewStopFacts, "function");
  const incident = {
    assets: [{ asset_id: "a", risk_score: 90 }],
    plan: { locations: [{ asset_id: "a", reported_can_self_evacuate: null }] },
    calls: [{ asset_id: "a", can_self_evacuate: true }],
  };
  const facts = review.crewStopFacts(incident, {
    ...tasks[1],
    ordering_reason: "Supplied directed-route optimization",
  });
  assert.equal(facts.selfEvacuation, "Unknown");
  assert.equal(facts.orderReason, "Supplied directed-route optimization");
  assert.equal(facts.risk, "Score 90");
});

test("connected risk facts show supplied probability and intersection without guessing a risk tier", () => {
  const incident = {
    assets: [{ asset_id: "a", burn_probability: 0.62, intersects_fire: true }],
    plan: { locations: [] },
    calls: [],
  };
  const facts = review.crewStopFacts(incident, tasks[1]);
  assert.match(facts.risk, /Fire intersects/);
  assert.match(facts.risk, /62%/);
  assert.doesNotMatch(facts.risk, /high|low|moderate/i);
});

test("drafts remove stale route topology and planner explanations with their old times", () => {
  const draft = review.draftCrewTasks([
    {
      status: "proposed",
      ordering_evidence: { criterion: "distance" },
      path_nodes: ["base", "a"],
      route_status: "available",
      from_node: "base",
    },
  ])[0];
  for (const key of [
    "ordering_evidence",
    "path_nodes",
    "route_status",
    "from_node",
  ])
    assert.equal(draft[key], undefined);
});

test("planner ordering evidence retains the actual heuristic and supplied criteria without turning benefit into outcomes", () => {
  const incident = { assets: [], plan: { locations: [] }, calls: [] };
  const facts = review.crewStopFacts(incident, {
    ordering_evidence: {
      method: "deterministic prerequisite-aware greedy heuristic",
      assisted_gain: 2,
      people_gain: 6,
      value_gain: 100,
      candidate_count: 6,
      selection_rank: 1,
      tie_break: "earliest finish, action ID, team ID",
      reason:
        "Highest lexicographic downstream assisted, people and value benefit among feasible candidates",
    },
  });
  assert.match(facts.orderReason, /greedy heuristic/);
  assert.match(facts.orderReason, /Assisted benefit: 2/);
  assert.match(facts.orderReason, /People benefit: 6/);
  assert.match(facts.orderReason, /Value benefit: 100/);
  assert.match(facts.orderReason, /earliest finish, action ID, team ID/);
  assert.match(facts.orderReason, /not predicted outcomes/);
});

test("analyst ordering evidence identifies the selected rank without inventing successful validation or action text", () => {
  const incident = { assets: [], plan: { locations: [] }, calls: [] };
  const facts = review.crewStopFacts(incident, {
    action_name: "Check access",
    ordering_evidence: { method: "analyst_requested_order", selection_rank: 2 },
  });
  assert.equal(facts.orderReason, "Analyst requested stop 2");
  assert.equal(facts.action, "Check access");
});

test("mission facts distinguish pickup from planned arrival without inventing delivery", () => {
  const incident = {
    assets: [],
    teams: [
      { team_id: "a", name: "Engine 12" },
      { team_id: "b", name: "Rescue 4" },
    ],
    calls: [],
    plan: { locations: [] },
  };
  assert.equal(typeof review.crewMissionFacts, "function");
  const pickup = review.crewMissionFacts(incident, {
    mission_status: "pickup_only",
  });
  assert.match(pickup.label, /Pickup only/);
  assert.equal(pickup.plannedPeople, "Unknown");
  const mission = review.crewMissionFacts(incident, {
    mission_status: "complete_evacuation",
    delivered_people: 16,
    team_ids: ["a", "b"],
    required_team_count: 2,
    evacuation: {
      destination_id: "shelter",
      unload_min: 3,
      places_reserved: 16,
      confirmed: true,
      safe: true,
    },
    mission_legs: [
      { kind: "transport", people: 8 },
      { kind: "return" },
      { kind: "transport", people: 8 },
    ],
  });
  assert.equal(mission.label, "Complete evacuation planned");
  assert.equal(mission.plannedPeople, "16");
  assert.equal(mission.crews, "Engine 12 · Rescue 4");
  assert.equal(mission.destination, "shelter");
  assert.equal(mission.legs.length, 3);
});

test("advanced missions and joint work prevent isolated reorder of the entire plan", () => {
  assert.equal(typeof review.crewReorderBlocked, "function");
  for (const extra of [
    { mission_status: "complete_evacuation" },
    { team_ids: ["a", "b"] },
    { required_team_count: 2 },
  ]) {
    const plan = [
      { ...tasks[1], ...extra },
      { ...tasks[2] },
      { ...tasks[1], action_id: "c" },
    ];
    assert.equal(review.crewReorderBlocked(plan), true);
    assert.equal(review.moveCrewStop(plan, 1, 1), plan);
  }
  assert.equal(review.crewReorderBlocked(tasks), false);
  assert.equal(
    review.crewReorderBlocked([{ ...tasks[1], mission_status: "pickup_only" }]),
    false,
  );
});

test("sensitivity keeps missing bounds unknown and fragile stress failures explicit", () => {
  assert.equal(typeof review.crewSensitivityFacts, "function");
  assert.equal(review.crewSensitivityFacts({}).label, "Sensitivity unknown");
  const fragile = review.crewSensitivityFacts({
    sensitivity: {
      status: "fragile",
      duration_high_min: 25,
      deadline_early_min: 20,
      stress_finish_min: 30,
      reasons: ["stress_deadline_exceeded"],
    },
  });
  assert.equal(fragile.label, "Fragile under supplied uncertainty");
  assert.equal(fragile.tone, "warning");
  assert.match(fragile.reasons, /Stress deadline exceeded/);
  assert.equal(
    review.crewSensitivityFacts({ sensitivity: { status: "robust" } }).label,
    "Passes supplied stress case",
  );
});

test("urgent reviews combine both channels without losing reasons or late candidate evidence", () => {
  assert.equal(typeof review.urgentInterventionReviews, "function");
  const result = review.urgentInterventionReviews({
    assets: [{ asset_id: "a", name: "Care home" }],
    plan: {
      response: {
        review: [
          {
            action_id: "rescue",
            asset_id: "a",
            reason: "urgent_intervention_review",
            human_decision_required: true,
            reasons: ["deadline_exceeded"],
          },
        ],
        unassigned: [
          {
            action_id: "rescue",
            asset_id: "a",
            reason: "urgent_intervention_review",
            candidate_attempts: [{ finish_min: 40, deadline_min: 30 }],
          },
          { asset_id: "b", reason: "ordinary" },
        ],
      },
    },
  });
  assert.equal(result.length, 1);
  assert.equal(result[0].name, "Care home");
  assert.deepEqual(result[0].reasons, ["deadline_exceeded"]);
  assert.equal(result[0].candidate_attempts[0].finish_min, 40);
});

test("known people benefit remains visible with unknown value dimensions", () => {
  const facts = review.crewStopFacts(
    { assets: [], calls: [], plan: { locations: [] } },
    {
      unknown_dimensions: ["replacement_value_eur"],
      ordering_evidence: { people_gain: 8, assisted_gain: 4, value_gain: null },
    },
  );
  assert.equal(facts.valuation, "Unknown");
  assert.match(facts.orderReason, /People benefit: 8/);
  assert.match(facts.unknownDimensions, /Replacement value eur/);
});

test("joint mission totals stay separate from this crew's planned arrivals and trip count stays sourced", () => {
  const facts = review.crewMissionFacts(
    { teams: [] },
    { delivered_people: 8, mission_delivered_people: 16, trip_count: 2 },
  );
  assert.equal(facts.plannedPeople, "8");
  assert.equal(facts.missionPeople, "16");
  assert.equal(facts.tripCount, "2");
  assert.equal(review.crewMissionFacts({}, {}).tripCount, "Unknown");
});
