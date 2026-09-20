"""Public planning evidence shared by publication and approval version binding."""

RESPONSE_TASK_DETAILS = (
    "mission_status",
    "mission_id",
    "delivered_people",
    "mission_delivered_people",
    "trip_count",
    "evacuation",
    "mission_legs",
    "team_ids",
    "required_team_count",
    "benefit_owner_team_id",
    "sensitivity",
    "unknown_dimensions",
    "duration_high_min",
    "deadline_early_min",
)
RESPONSE_REVIEW_DETAILS = (
    "requested_transport_people",
    "remaining_people",
    "human_decision_required",
    "candidate_attempts",
    "unknown_dimensions",
    "sensitivity",
    "required_team_count",
    "team_ids",
    "mission_id",
)
RESPONSE_PUBLIC_FIELDS = set(
    RESPONSE_TASK_DETAILS
    + RESPONSE_REVIEW_DETAILS
    + (
        "unload_min",
        "places_reserved",
        "arrive_min",
        "stress_finish_min",
        "stress_deadline_min",
        "remaining_needs",
        "original_people",
        "remaining_people",
        "original_assisted",
        "remaining_assisted",
        "confirmed_arrived",
        "objective",
        "search",
        "depth",
        "beam_width",
        "max_expansions",
        "expansions",
        "truncated",
        "evaluation_count",
        "sensitivity_summary",
        "search_limits",
        "coverage_by_dimension",
    )
)


def joint_action_ids(teams):
    """Allow repeated action IDs only for a complete, consistent joint reservation."""
    groups = {}
    for team in teams:
        for task in team["tasks"]:
            groups.setdefault(task["action_id"], []).append(task)
    joint = set()
    for action_id, rows in groups.items():
        first = rows[0]
        count = first.get("required_team_count", 1)
        if len(rows) == 1 and count == 1:
            continue
        members = first.get("team_ids")
        if (
            type(count) is not int
            or count < 2
            or not isinstance(members, list)
            or any(not isinstance(t, str) or not t for t in members)
            or len(members) != count
            or len(set(members)) != count
            or len(rows) != count
            or set(members) != {r["team_id"] for r in rows}
            or first.get("mission_id") != action_id
            or first.get("benefit_owner_team_id") not in members
        ):
            raise ValueError("invalid joint crew reservation membership")
        identity = (
            "mission_id",
            "asset_id",
            "scenario_id",
            "snapshot_id",
            "action_version",
            "status",
            "start_min",
            "required_team_count",
            "team_ids",
            "benefit_owner_team_id",
            "effects",
        )
        if any(any(row.get(k) != first.get(k) for k in identity) for row in rows[1:]):
            raise ValueError("inconsistent joint crew reservation evidence")
        joint.add(action_id)
    return joint
