/** A preview is usable only for the exact displayed context and requested order. */
export function validateCrewPreview(data, expected, actionIds) {
  if (
    !data ||
    Object.entries(expected).some(([key, value]) => data[key] !== value) ||
    typeof data.review_version !== "string" ||
    !data.review_version ||
    typeof data.can_confirm !== "boolean" ||
    !Array.isArray(data.blockers) ||
    !Array.isArray(data.reviewed_plan?.tasks) ||
    data.reviewed_plan.team_id !== expected.team_id ||
    JSON.stringify(data.reviewed_plan.action_ids) !==
      JSON.stringify(actionIds) ||
    JSON.stringify(data.reviewed_plan.tasks.map((task) => task.action_id)) !==
      JSON.stringify(actionIds)
  ) {
    throw new Error(
      "The plan changed. Validate the latest order again before confirming.",
    );
  }
  return data;
}
