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
