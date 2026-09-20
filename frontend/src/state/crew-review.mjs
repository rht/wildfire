import {
  count,
  humanize,
  money,
  number,
  readinessFacts,
  percentage,
} from "./model.mjs";

const known = (value) =>
  typeof value === "string" && value.trim() ? value : "Unknown";
const yesNo = (value) =>
  value === true ? "Yes" : value === false ? "No" : "Unknown";

export function moveCrewStop(tasks, index, delta) {
  const next = index + delta;
  if (
    ![1, -1].includes(delta) ||
    tasks[index]?.status !== "proposed" ||
    tasks[next]?.status !== "proposed"
  )
    return tasks;
  const moved = [...tasks];
  [moved[index], moved[next]] = [moved[next], moved[index]];
  return moved;
}

export function draftCrewTasks(tasks) {
  return tasks.map((task) => {
    if (task.status !== "proposed") return task;
    const draft = { ...task };
    for (const field of [
      "path_lonlat",
      "route_source",
      "route_id",
      "depart_min",
      "travel_min",
      "arrival_min",
      "start_min",
      "finish_min",
      "ordering_reason",
      "ordering_evidence",
      "path_nodes",
      "route_status",
      "from_node",
    ])
      delete draft[field];
    return draft;
  });
}

function suppliedOrderReason(task, team) {
  const evidence = task.ordering_evidence;
  if (evidence?.method === "analyst_requested_order")
    return `Analyst requested stop ${number(evidence.selection_rank) === null ? "Unknown" : count(evidence.selection_rank)}`;
  if (evidence && typeof evidence === "object") {
    const criteria = [
      ["Assisted benefit", evidence.assisted_gain],
      ["People benefit", evidence.people_gain],
      ["Value benefit", evidence.value_gain],
      ["Candidate count", evidence.candidate_count],
      ["Selection rank", evidence.selection_rank],
    ]
      .filter(([, value]) => number(value) !== null)
      .map(([label, value]) => `${label}: ${count(value)}`);
    return (
      [
        evidence.reason,
        evidence.method,
        ...criteria,
        evidence.tie_break ? `Tie break: ${evidence.tie_break}` : null,
        criteria.length
          ? "Heuristic benefit values, not predicted outcomes."
          : null,
      ]
        .filter((value) => typeof value === "string" && value.trim())
        .join(" · ") || "Unknown"
    );
  }
  return known(
    task.ordering_reason ||
      team.ordering_reason ||
      team.planning_context?.ordering_reason,
  );
}

export function crewStopFacts(incident, task, team = {}) {
  const asset =
    incident.assets.find((item) => item.asset_id === task.asset_id) || {};
  const calls = (incident.calls || []).filter(
    (call) => call.asset_id === task.asset_id,
  );
  const readiness = readinessFacts(incident, {
    asset_id: task.asset_id,
    calls,
  });
  const riskFacts = [
    asset.risk_label && asset.risk_label !== "Not assessed"
      ? asset.risk_label
      : null,
    asset.intersects_fire === true ? "Fire intersects" : null,
    number(asset.burn_probability) !== null
      ? `Burn probability ${percentage(asset.burn_probability)}`
      : null,
  ].filter(Boolean);
  return {
    asset,
    action:
      typeof task.action === "string" && task.action
        ? humanize(task.action)
        : known(task.action_name),
    mobility: known(asset.mobility),
    selfEvacuation: yesNo(readiness.canSelfEvacuate),
    assistance: yesNo(readiness.needsAssistance),
    transport: yesNo(readiness.transportAvailable),
    evidenceSource: readiness.source || "Unknown",
    conflicts: readiness.conflicts,
    people:
      number(asset.estimated_occupancy) === null
        ? "Unknown"
        : count(asset.estimated_occupancy),
    occupancyBasis: known(asset.occupancy_basis),
    valuation:
      number(asset.replacement_value_eur) === null
        ? "Unknown"
        : money(asset.replacement_value_eur),
    valuationBasis: known(asset.replacement_value_basis),
    risk:
      riskFacts.join(" · ") ||
      (number(asset.risk_score) !== null
        ? `Score ${count(asset.risk_score)}`
        : "Unknown"),
    riskScore:
      number(asset.risk_score) === null ? "Unknown" : count(asset.risk_score),
    orderReason: suppliedOrderReason(task, team),
  };
}
