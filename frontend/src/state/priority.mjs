import { callRows, readinessFacts, crewUrgency, number } from "./model.mjs";

export function assistanceTotals(incidents) {
  const locations = new Map();
  for (const incident of incidents) {
    for (const row of callRows(incident)) {
      const facts = readinessFacts(incident, row);
      const entry = locations.get(row.asset_id) || {
        values: new Set(),
        review: false,
      };
      if (facts.needsAssistance !== null)
        entry.values.add(facts.needsAssistance);
      entry.review ||=
        facts.assistanceReviewRequired ||
        facts.conflicts.includes("needs_assistance");
      locations.set(row.asset_id, entry);
    }
  }
  let confirmed = 0,
    pending = 0;
  for (const entry of locations.values()) {
    if (entry.review || entry.values.size > 1) pending++;
    else if (entry.values.has(true)) confirmed++;
  }
  return { total: confirmed + pending, confirmed, pending };
}

export function locationPriority(incident, assetId) {
  const contact = incident.contacts.ranked?.find(
    (row) => row.asset_id === assetId,
  );
  const window = number(contact?.slack_min);
  return {
    order: window ?? Infinity,
    label:
      window === null
        ? "Window not supplied"
        : window <= 0
          ? "Evacuation window exhausted"
          : `${window} min remaining`,
  };
}

export function resourcePriority(team) {
  const urgency = crewUrgency(team?.tasks || []);
  return { order: urgency.margin ?? Infinity, label: urgency.label };
}

export function priorityList(rows) {
  return [...rows]
    .sort((a, b) => a.priority.order - b.priority.order)
    .map((row, index) => ({
      ...row,
      priorityRank: Number.isFinite(row.priority.order) ? index + 1 : null,
    }));
}
