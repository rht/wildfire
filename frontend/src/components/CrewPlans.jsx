import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Typography, Alert, Chip } from "@mui/material";
import { useApprovals } from "../state/approvals";
import CrewPlanMap from "./CrewPlanMap";
import { MainCard, Empty, DetailDialog, Coordinates } from "./Common";
import {
  responseTeams,
  humanize,
  count,
  number,
  stamp,
  crewUrgency,
} from "../state/model.mjs";

import { crewMapData, crewStopId } from "../state/crew-map.mjs";
import {
  crewStopFacts,
  crewMissionFacts,
  crewSensitivityFacts,
  crewReorderBlocked,
  urgentInterventionReviews,
  draftCrewTasks,
  moveCrewStop,
} from "../state/crew-review.mjs";

const minutes = (value) =>
  number(value) === null ? "Unknown" : `${count(value)} min`;
const incidentContext = (incident) =>
  JSON.stringify([incident.id, incident.snapshot_id, incident.revision]);

function StopFact({ label, children }) {
  return (
    <div className="crew-stop-fact">
      <span>{label}: </span>
      <strong>{children}</strong>
    </div>
  );
}

export function UrgentInterventionReviews({ incident }) {
  const reviews = urgentInterventionReviews(incident);
  if (!reviews.length) return null;
  const urgent = reviews.some(
    (review) => review.reason === "urgent_intervention_review",
  );
  return (
    <Alert severity={urgent ? "error" : "warning"} sx={{ my: 2 }}>
      <strong>
        {urgent ? "Urgent intervention review" : "Intervention review"}
      </strong>
      <div>
        Human decision required. Review the supplied reasons before confirming a
        plan.
      </div>
      {reviews.map((review, index) => (
        <div key={review.action_id || review.asset_id || index}>
          <strong>{review.name}</strong>
          {review.reasons.some((reason) =>
            ["deadline", "deadline_exceeded"].includes(reason),
          ) && (
            <div>
              A missed planning deadline does not resolve the need for
              intervention. Review rescue, access and assistance options.
            </div>
          )}
          <div>
            {review.reasons.map(humanize).join(" · ") ||
              "Intervention unresolved"}
          </div>
          {review.candidate_attempts.map((attempt, attemptIndex) => (
            <div key={attemptIndex}>
              Candidate {attemptIndex + 1}
              {attempt.team_id
                ? ` · ${incident.teams?.find((team) => team.team_id === attempt.team_id)?.name || attempt.team_id}`
                : ""}
              {" · "}Finish {minutes(attempt.finish_min)} · deadline{" "}
              {minutes(attempt.deadline_min)}
              {attempt.reason ? ` · ${humanize(attempt.reason)}` : ""}
            </div>
          ))}
        </div>
      ))}
    </Alert>
  );
}

export function CrewStopTable({
  incident,
  team,
  activeStopId,
  onActiveStopChange,
  onMove,
  editable,
  draft,
  showOrderControls = true,
}) {
  const { start } = crewMapData(team, incident.assets);
  const startDetails =
    start || team.starting_location || team.planning_context?.start || {};
  const taskRows = team.tasks || [];
  const coordinatedReview = crewReorderBlocked(taskRows);
  const rowEvents = (id) => ({
    tabIndex: 0,
    "data-stop-id": id,
    className: `crew-stop-row${activeStopId === id ? " is-active" : ""}`,
    onMouseEnter: () => onActiveStopChange(id),
    onFocus: () => onActiveStopChange(id),
    onClick: () => onActiveStopChange(id),
  });
  return (
    <div
      className="crew-stop-table-wrap"
      tabIndex={0}
      role="region"
      aria-label="Crew visit order and evidence"
    >
      <table className="crew-stop-table">
        <caption>
          Visit order · starting point, then numbered destinations. Times are
          minutes from the plan’s time origin.
        </caption>
        <thead>
          <tr>
            <th scope="col">Stop / action</th>
            <th scope="col">People & assistance</th>
            <th scope="col">Risk & valuation</th>
            <th scope="col">Travel & timing</th>
            <th scope="col">Order & prerequisites</th>
          </tr>
        </thead>
        <tbody>
          <tr {...rowEvents("__start__")}>
            <th scope="row">
              <div className="crew-stop-heading">
                Start ·{" "}
                {startDetails.name ||
                  startDetails.node_id ||
                  (typeof team.start_node === "string"
                    ? team.start_node
                    : "Unknown")}
              </div>
              <div>
                {start?.kind === "reported"
                  ? "Reported current position"
                  : "Planned starting point"}
              </div>
              {start ? (
                <Coordinates location={start} />
              ) : (
                <StopFact label="Coordinates">Unknown</StopFact>
              )}
              <StopFact label="Source">
                {startDetails.source || "Unknown"}
              </StopFact>
              <StopFact label="Observed">
                {startDetails.observed_at
                  ? stamp(startDetails.observed_at)
                  : "Unknown"}
              </StopFact>
            </th>
            <td>Not a destination</td>
            <td>Not a destination</td>
            <td>
              <StopFact label="Available">
                {minutes(startDetails.available_min)}
              </StopFact>
            </td>
            <td>Starting point is fixed.</td>
          </tr>
          {taskRows.map((task, index) => {
            const id = crewStopId(task, index),
              facts = crewStopFacts(incident, task, draft ? {} : team),
              mission = crewMissionFacts(incident, task),
              sensitivity = crewSensitivityFacts(task);
            const name =
              facts.asset.name || task.asset_id || "Unknown destination";
            const unavailable = draft && task.status === "proposed";
            const timing = (value) =>
              unavailable ? "Unavailable until validation" : minutes(value);
            return (
              <tr key={id} {...rowEvents(id)}>
                <th scope="row">
                  <div className="crew-stop-heading">
                    {index + 1}. {name}
                  </div>
                  <Coordinates location={facts.asset} />
                  <StopFact label="Action">{facts.action}</StopFact>
                  {task.mission_status && (
                    <StopFact label="Mission">{mission.label}</StopFact>
                  )}
                  {(task.team_ids?.length > 1 ||
                    task.required_team_count > 1) && (
                    <>
                      <StopFact label="Joint crews">{mission.crews}</StopFact>
                      <StopFact label="Crews required">
                        {number(task.required_team_count) === null
                          ? "Unknown"
                          : count(task.required_team_count)}
                      </StopFact>
                    </>
                  )}
                  <StopFact label="Status">
                    {task.status ? humanize(task.status) : "Unknown"}
                  </StopFact>
                </th>
                <td>
                  <StopFact label="Expected people">{facts.people}</StopFact>
                  {task.mission_status === "complete_evacuation" && (
                    <>
                      <StopFact label="Planned arrivals · this crew">
                        {mission.plannedPeople}
                      </StopFact>
                      <StopFact label="Planned arrivals · whole mission">
                        {mission.missionPeople}
                      </StopFact>
                      <div>
                        Plan estimates only; actual arrivals require
                        confirmation.
                      </div>
                    </>
                  )}
                  <StopFact label="Occupancy basis">
                    {facts.occupancyBasis}
                  </StopFact>
                  <StopFact label="Mobility">{facts.mobility}</StopFact>
                  <StopFact label="Can self-evacuate">
                    {facts.selfEvacuation}
                  </StopFact>
                  <StopFact label="Assistance needed">
                    {facts.assistance}
                  </StopFact>
                  <StopFact label="Transport available">
                    {facts.transport}
                  </StopFact>
                  <StopFact label="Evidence">{facts.evidenceSource}</StopFact>
                  {facts.conflicts.length > 0 && (
                    <div className="confirmation-pending">
                      Conflicting reports:{" "}
                      {facts.conflicts.map(humanize).join(", ")}
                    </div>
                  )}
                </td>
                <td>
                  <StopFact label="Risk">{facts.risk}</StopFact>
                  <StopFact label="Risk score">{facts.riskScore}</StopFact>
                  <StopFact label="Replacement value">
                    {facts.valuation}
                  </StopFact>
                  {facts.unknownDimensions && (
                    <StopFact label="Unknown planning inputs">
                      {facts.unknownDimensions}
                    </StopFact>
                  )}
                  <StopFact label="Value basis">
                    {facts.valuationBasis}
                  </StopFact>
                  <StopFact label="Assessment">
                    {facts.asset.assessed_at
                      ? stamp(facts.asset.assessed_at)
                      : "Unknown"}
                  </StopFact>
                </td>
                <td>
                  {task.mission_status === "complete_evacuation" && (
                    <>
                      <StopFact label="Reception destination">
                        {mission.destination}
                      </StopFact>
                      <StopFact label="Planned trips">
                        {mission.tripCount}
                      </StopFact>
                      <StopFact label="Unload per trip">
                        {minutes(task.evacuation?.unload_min)}
                      </StopFact>
                      <StopFact label="Reception places reserved">
                        {number(task.evacuation?.places_reserved) === null
                          ? "Unknown"
                          : count(task.evacuation.places_reserved)}
                      </StopFact>
                      <StopFact label="Reception available until">
                        {minutes(task.evacuation?.available_until_min)}
                      </StopFact>
                      <StopFact label="Reception evidence">
                        {task.evacuation?.source || "Unknown"}
                      </StopFact>
                    </>
                  )}
                  {mission.legs.map((leg, legIndex) => (
                    <div key={legIndex}>
                      <strong>
                        {legIndex + 1}. {humanize(leg.kind || "leg")}
                      </strong>
                      <div>
                        {leg.from_node || "Unknown"} →{" "}
                        {leg.to_node || "Unknown"}
                      </div>
                      <StopFact label="Depart / arrive / finish">
                        {timing(leg.depart_min)} / {timing(leg.arrive_min)} /{" "}
                        {timing(leg.finish_min)}
                      </StopFact>
                      {number(leg.people) !== null && (
                        <StopFact label="People planned">
                          {count(leg.people)}
                        </StopFact>
                      )}
                      <StopFact label="Leg route">
                        {leg.route_source || "Unknown"}
                      </StopFact>
                    </div>
                  ))}
                  <StopFact label="Departure">
                    {timing(task.depart_min)}
                  </StopFact>
                  <StopFact label="Travel">{timing(task.travel_min)}</StopFact>
                  <StopFact label="Arrival / work start">
                    {timing(task.start_min)}
                  </StopFact>
                  <StopFact label="Finish">{timing(task.finish_min)}</StopFact>
                  <StopFact label="Deadline">
                    {minutes(task.deadline_min)}
                  </StopFact>
                  <StopFact label="Route">
                    {unavailable
                      ? "Unavailable until validation"
                      : task.route_source || "Unknown"}
                  </StopFact>
                </td>
                <td>
                  <Chip
                    size="small"
                    variant="outlined"
                    color={sensitivity.tone}
                    label={sensitivity.label}
                  />
                  {sensitivity.reasons && <div>{sensitivity.reasons}</div>}
                  {task.sensitivity && (
                    <>
                      <StopFact label="Long duration bound">
                        {minutes(task.sensitivity.duration_high_min)}
                      </StopFact>
                      <StopFact label="Early deadline bound">
                        {minutes(task.sensitivity.deadline_early_min)}
                      </StopFact>
                      {(number(task.sensitivity.stress_finish_min) !== null ||
                        number(task.sensitivity.stress_deadline_min) !==
                          null) && (
                        <StopFact label="Stress finish / deadline">
                          {minutes(task.sensitivity.stress_finish_min)} /{" "}
                          {minutes(task.sensitivity.stress_deadline_min)}
                        </StopFact>
                      )}
                    </>
                  )}
                  <StopFact label="Order reason">
                    {unavailable
                      ? "Analyst draft · awaiting validation"
                      : facts.orderReason}
                  </StopFact>
                  <StopFact label="Prerequisites">
                    {unavailable ? "Awaiting validation · " : ""}
                    {Array.isArray(task.prerequisites)
                      ? task.prerequisites.map(humanize).join(" · ") ||
                        "None supplied"
                      : "Unknown"}
                  </StopFact>
                  {showOrderControls &&
                    coordinatedReview &&
                    task.status === "proposed" && (
                      <div>
                        Coordinated review required. Replan the complete mission
                        and all participating crews before changing this order.
                      </div>
                    )}
                  {showOrderControls && task.status === "proposed" ? (
                    <div className="crew-stop-order-controls">
                      <Button
                        size="small"
                        variant="outlined"
                        aria-label={`Move ${name} up`}
                        disabled={
                          !editable ||
                          coordinatedReview ||
                          taskRows[index - 1]?.status !== "proposed"
                        }
                        onClick={() => onMove(index, -1)}
                      >
                        Up
                      </Button>
                      <Button
                        size="small"
                        variant="outlined"
                        aria-label={`Move ${name} down`}
                        disabled={
                          !editable ||
                          coordinatedReview ||
                          taskRows[index + 1]?.status !== "proposed"
                        }
                        onClick={() => onMove(index, 1)}
                      >
                        Down
                      </Button>
                    </div>
                  ) : task.status !== "proposed" ? (
                    <div>
                      Fixed ·{" "}
                      {task.status ? humanize(task.status) : "status unknown"}
                    </div>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const needsConfirmation = (tasks) =>
  tasks.some((step) => step.status === "proposed");

export default function CrewPlans({ incidents, teamId, buttonLabel }) {
  const approvals = useApprovals();
  const [selectedKey, setSelectedKey] = useState(null),
    [reviewedVersion, setReviewedVersion] = useState(null),
    [reviewedContext, setReviewedContext] = useState(null),
    [saveError, setSaveError] = useState(null),
    [draft, setDraft] = useState(null),
    [activeStopId, setActiveStopId] = useState("__start__");
  const previewRequest = useRef(0);
  const tableRef = useRef(null);
  const open = (row) => {
    previewRequest.current += 1;
    setDraft(null);
    setActiveStopId("__start__");
    setSelectedKey(row?.key);
    setReviewedVersion(row?.confirmation.plan?.plan_version || null);
    setReviewedContext(row ? incidentContext(row.incident) : null);
    setSaveError(null);
  };
  const rows = useMemo(() => {
    const result = incidents.flatMap((incident) => {
      const plans = responseTeams(incident);
      const ids = [
        ...new Set([
          ...incident.teams.map((t) => t.team_id),
          ...plans.map((t) => t.team_id),
        ]),
      ];
      return ids.map((id) => ({
        key: `${incident.id}:${id}`,
        incident,
        id,
        name: incident.teams.find((t) => t.team_id === id)?.name || id,
        tasks: plans.find((t) => t.team_id === id)?.tasks || [],
        team: plans.find((t) => t.team_id === id),
      }));
    });
    for (const row of result) {
      row.confirmation = approvals.info(row.incident, row.id);
      if (row.confirmation.plan?.reviewed_plan) {
        row.team = { ...row.team, ...row.confirmation.plan.reviewed_plan };
        row.tasks = row.team.tasks || [];
      }
    }
    return result;
  }, [incidents, approvals]);
  const selected = rows.find((r) => r.key === selectedKey);
  const savedApproval = selected?.confirmation.plan?.approval;
  const approvalIdentity = JSON.stringify([
    savedApproval?.approval_id || null,
    savedApproval?.review_version || null,
  ]);
  const awaiting = rows.filter(
    (row) =>
      needsConfirmation(row.tasks) &&
      row.confirmation.phase === "ready" &&
      row.confirmation.plan?.can_confirm &&
      !row.confirmation.plan?.approval,
  ).length;
  const pending = (row) =>
    needsConfirmation(row.tasks) && !row.confirmation.plan?.approval;
  const changed =
    selected &&
    reviewedVersion !== null &&
    selected.confirmation.plan?.plan_version &&
    (reviewedVersion !== selected.confirmation.plan.plan_version ||
      reviewedContext !== incidentContext(selected.incident));
  useEffect(() => {
    if (
      selected &&
      reviewedVersion === null &&
      selected.confirmation.plan?.plan_version
    )
      setReviewedVersion(selected.confirmation.plan.plan_version);
  }, [selected, reviewedVersion]);
  const validDraft =
    selected &&
    !changed &&
    draft?.key === selected.key &&
    draft.context === incidentContext(selected.incident) &&
    draft.version === reviewedVersion
      ? draft
      : null;
  useEffect(() => {
    if (
      draft &&
      (!selected ||
        selected.confirmation.phase === "historical" ||
        draft.key !== selected.key ||
        draft.context !== incidentContext(selected.incident) ||
        changed)
    ) {
      previewRequest.current += 1;
      setDraft(null);
    }
  }, [draft, selected, changed]);
  const approvalChanged =
    !!validDraft &&
    selected.confirmation.phase === "ready" &&
    validDraft.approvalIdentity !== approvalIdentity &&
    (!validDraft.preview?.review_version ||
      validDraft.preview.review_version !== savedApproval?.review_version);
  useEffect(() => {
    if (!approvalChanged) return;
    previewRequest.current += 1;
    setDraft((previous) =>
      previous
        ? { ...previous, approvalIdentity, preview: null, validating: false }
        : null,
    );
    setSaveError(
      "The saved approval changed. Validate your requested order again before confirming.",
    );
  }, [approvalChanged, approvalIdentity]);
  const preview = approvalChanged ? null : validDraft?.preview;
  const shownTeam = useMemo(
    () =>
      selected
        ? {
            ...selected.team,
            ...(preview?.reviewed_plan || {}),
            tasks:
              preview?.reviewed_plan?.tasks ||
              (validDraft ? draftCrewTasks(validDraft.tasks) : selected.tasks),
          }
        : null,
    [selected, preview, validDraft],
  );
  const editable =
    selected?.confirmation.phase === "ready" &&
    selected.confirmation.plan?.can_confirm &&
    !selected.confirmation.saving &&
    !changed &&
    !!reviewedVersion &&
    !validDraft?.validating &&
    !crewReorderBlocked(selected?.tasks);
  const move = (index, direction) => {
    if (!editable) return;
    const tasks = moveCrewStop(
      validDraft?.tasks || selected.tasks,
      index,
      direction,
    );
    previewRequest.current += 1;
    setDraft({
      key: selected.key,
      context: incidentContext(selected.incident),
      version: reviewedVersion,
      approvalIdentity,
      tasks,
      preview: null,
      validating: false,
    });
    setSaveError(null);
  };
  const validateOrder = async () => {
    if (!editable || !validDraft) return;
    const request = ++previewRequest.current;
    setSaveError(null);
    setDraft({ ...validDraft, validating: true });
    try {
      const result = await approvals.preview(
        selected.incident,
        selected.id,
        reviewedVersion,
        validDraft.tasks.map((task) => task.action_id),
      );
      if (request === previewRequest.current)
        setDraft({ ...validDraft, validating: false, preview: result });
    } catch (error) {
      if (request === previewRequest.current) {
        setDraft({ ...validDraft, validating: false });
        setSaveError(error.message);
      }
    }
  };
  const selectFromMap = (id) => {
    setActiveStopId(id);
    const row = [
      ...(tableRef.current?.querySelectorAll("[data-stop-id]") || []),
    ].find((item) => item.dataset.stopId === id);
    const container = tableRef.current?.querySelector(".crew-stop-table-wrap");
    if (row && container) {
      const bounds = container.getBoundingClientRect(),
        target = row.getBoundingClientRect();
      const heading =
        container.querySelector("thead")?.getBoundingClientRect().height || 0;
      if (target.top < bounds.top + heading)
        container.scrollTop += target.top - bounds.top - heading;
      else if (target.bottom > bounds.bottom)
        container.scrollTop += target.bottom - bounds.bottom;
    }
  };
  const save = async () => {
    const request = ++previewRequest.current;
    setSaveError(null);
    try {
      await approvals.confirm(
        selected.incident,
        selected.id,
        reviewedVersion,
        preview
          ? {
              ...preview,
              action_ids: validDraft.tasks.map((task) => task.action_id),
            }
          : null,
      );
      if (request === previewRequest.current) setDraft(null);
    } catch (error) {
      if (request === previewRequest.current) {
        setDraft((previous) =>
          previous ? { ...previous, preview: null, validating: false } : null,
        );
        setSaveError(error.message);
      }
    }
  };
  const teamRow = teamId ? rows.find((row) => row.id === teamId) : null;
  const confirmationButton =
    selected && (pending(selected) || validDraft) ? (
      <Button
        variant="contained"
        disabled={
          selected.confirmation.phase !== "ready" ||
          !selected.confirmation.plan?.can_confirm ||
          selected.confirmation.saving ||
          !!changed ||
          !reviewedVersion ||
          (!!validDraft && !preview?.can_confirm)
        }
        onClick={save}
      >
        {selected.confirmation.saving
          ? "Saving confirmation…"
          : "Confirm crew plan"}
      </Button>
    ) : null;
  return (
    <>
      {teamId ? (
        <Button
          variant="outlined"
          size="small"
          onClick={() => open(teamRow)}
          aria-label={`Review plan for ${teamRow?.name || teamId}`}
        >
          {buttonLabel ||
            (teamRow?.confirmation.plan?.approval
              ? "Confirmed · view"
              : teamRow?.confirmation.plan?.can_confirm
                ? "Review & confirm"
                : "Review plan")}
        </Button>
      ) : (
        <MainCard
          title="Crew plans"
          action={
            <Chip
              size="small"
              color={awaiting ? "warning" : "default"}
              label={
                awaiting
                  ? `${awaiting} need confirmation`
                  : `${rows.length} crews`
              }
            />
          }
        >
          {awaiting > 0 && (
            <div className="crew-confirmation-intro">
              Fire analyst confirmation required for each proposed crew plan.
            </div>
          )}
          <div className="crew-plan-list">
            {rows.map((row) => (
              <Button
                key={row.key}
                className="crew-plan-entry"
                onClick={() => open(row)}
                aria-label={`Review plan for ${row.name}`}
              >
                <span className="crew-identity">
                  <strong>{row.name}</strong>
                  <small>{row.incident.name}</small>
                  {row.confirmation.plan?.approval ? (
                    <small className="confirmation-saved">
                      Confirmed by {row.confirmation.plan.approval.analyst}
                    </small>
                  ) : (
                    needsConfirmation(row.tasks) && (
                      <small className="confirmation-pending">
                        {row.confirmation.phase === "ready"
                          ? row.confirmation.plan?.can_confirm
                            ? "Awaiting confirmation"
                            : "Crew not identified"
                          : row.confirmation.phase === "historical"
                            ? "Historical view"
                            : row.confirmation.phase === "loading"
                              ? "Checking confirmation…"
                              : "Confirmation unavailable"}
                      </small>
                    )
                  )}
                </span>
                <Chip
                  className="crew-urgency"
                  size="small"
                  variant="outlined"
                  color={crewUrgency(row.tasks).tone}
                  label={crewUrgency(row.tasks).label}
                />
                <span className="crew-step-count">
                  {row.tasks.length
                    ? pending(row) && row.confirmation.plan?.can_confirm
                      ? "Review & confirm →"
                      : `${row.tasks.length} steps · Review`
                    : "No plan supplied"}
                </span>
              </Button>
            ))}
          </div>
          {!rows.length && <Empty title="No crews supplied" />}
        </MainCard>
      )}
      <DetailDialog
        fullScreen
        backNavigation
        headerAction={confirmationButton}
        maxWidth="xl"
        className="crew-plan-review-dialog"
        open={!!selected}
        title={selected ? `Review crew plan · ${selected.name}` : ""}
        onClose={() => {
          previewRequest.current += 1;
          setDraft(null);
          setSelectedKey(null);
        }}
      >
        {selected && (
          <>
            <div className="crew-review-summary">
              <UrgentInterventionReviews incident={selected.incident} />
              {selected.confirmation.plan?.approval && !validDraft && (
                <Alert severity="success" sx={{ mb: 2 }}>
                  <strong>
                    Confirmed by {selected.confirmation.plan.approval.analyst}
                  </strong>
                  <br />
                  {stamp(selected.confirmation.plan.approval.confirmed_at)}
                  <br />
                  Confirmation recorded. Dispatch and prerequisite status are
                  unchanged.
                </Alert>
              )}
              {changed && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                  This plan changed since you opened it. Review the updated
                  steps before confirming.
                  <Button
                    onClick={() => {
                      setReviewedVersion(
                        selected.confirmation.plan.plan_version,
                      );
                      setReviewedContext(incidentContext(selected.incident));
                      previewRequest.current += 1;
                      setDraft(null);
                      setSaveError(null);
                    }}
                  >
                    Review updated plan
                  </Button>
                </Alert>
              )}
              <Typography className="crew-review-context">
                {selected.incident.name} · Timing:{" "}
                {validDraft && !preview
                  ? "Draft · awaiting validation"
                  : crewUrgency(shownTeam.tasks).label}
                . Margin compares the supplied deadline with the planned finish.
              </Typography>
              {(pending(selected) || validDraft) && (
                <div className="crew-review-status">
                  <Typography
                    color="text.secondary"
                    className="crew-review-confirmation-status"
                  >
                    {selected.confirmation.phase === "historical"
                      ? "Historical view. Return to current state to confirm this crew plan."
                      : selected.confirmation.phase === "loading"
                        ? "Checking saved confirmations…"
                        : selected.confirmation.phase === "error"
                          ? selected.confirmation.error
                          : !selected.confirmation.plan?.can_confirm
                            ? "A crew identifier and current proposed plan are needed before confirmation is available."
                            : `Confirm this crew plan as ${selected.confirmation.analyst}. This records approval; dispatch remains separate.`}
                  </Typography>
                  {saveError && <Alert severity="error">{saveError}</Alert>}
                  {selected.confirmation.phase === "error" && (
                    <>
                      <Button
                        onClick={() => approvals.refresh(selected.incident)}
                      >
                        Retry
                      </Button>
                      <Button onClick={() => window.location.reload()}>
                        Reload latest plan
                      </Button>
                    </>
                  )}
                </div>
              )}
            </div>
            <div className="crew-review-layout">
              <CrewPlanMap
                team={shownTeam}
                assets={selected.incident.assets}
                name={selected.name}
                activeStopId={activeStopId}
                onActiveStopChange={selectFromMap}
              />
              <div className="crew-review-steps">
                {validDraft && (
                  <div className="crew-review-toolbar">
                    <Alert
                      className="crew-review-state"
                      severity={
                        preview?.can_confirm
                          ? "success"
                          : preview
                            ? "warning"
                            : "info"
                      }
                    >
                      {validDraft.validating
                        ? "Validating routes, timing, prerequisites, capacity and deadlines…"
                        : preview?.can_confirm
                          ? "Validated order · review the recalculated plan before confirming."
                          : preview
                            ? "Order cannot be approved. Resolve the validation blockers or restore the supplied order."
                            : "Draft order · routes and timing are unavailable until validation."}
                      {preview?.blockers?.length > 0 && (
                        <ul>
                          {preview.blockers.map((blocker, index) => (
                            <li key={index}>
                              {blocker.reason || humanize(blocker.code)}
                            </li>
                          ))}
                        </ul>
                      )}
                    </Alert>
                    <Button
                      variant="outlined"
                      onClick={validateOrder}
                      disabled={!editable || !!preview}
                    >
                      Validate order
                    </Button>
                    <Button
                      onClick={() => {
                        previewRequest.current += 1;
                        setDraft(null);
                        setSaveError(null);
                      }}
                      disabled={selected.confirmation.saving}
                    >
                      Restore supplied order
                    </Button>
                  </div>
                )}
                <div className="crew-review-table-panel" ref={tableRef}>
                  <CrewStopTable
                    incident={selected.incident}
                    team={shownTeam}
                    activeStopId={activeStopId}
                    onActiveStopChange={setActiveStopId}
                    onMove={move}
                    editable={editable}
                    draft={!!validDraft && !preview}
                  />
                </div>
                {!selected.tasks.length && (
                  <Empty title="No plan supplied for this crew" />
                )}
              </div>
            </div>
          </>
        )}
      </DetailDialog>
    </>
  );
}
