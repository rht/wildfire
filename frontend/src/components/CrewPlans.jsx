import { useEffect, useState } from "react";
import { Button, Typography, Alert, Chip } from "@mui/material";
import { useApprovals } from "../state/approvals";
import { MainCard, Empty, DetailDialog, Coordinates, Facts } from "./Common";
import {
  responseTeams,
  humanize,
  count,
  stamp,
  crewUrgency,
} from "../state/model.mjs";

const needsConfirmation = (tasks) =>
  tasks.some((step) => step.status === "proposed");

export default function CrewPlans({ incidents, teamId }) {
  const approvals = useApprovals();
  const [selectedKey, setSelectedKey] = useState(null),
    [reviewedVersion, setReviewedVersion] = useState(null),
    [saveError, setSaveError] = useState(null);
  const open = (row) => {
    setSelectedKey(row?.key);
    setReviewedVersion(row?.confirmation.plan?.plan_version || null);
    setSaveError(null);
  };
  const rows = incidents.flatMap((incident) => {
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
    }));
  });
  for (const row of rows)
    row.confirmation = approvals.info(row.incident, row.id);
  const selected = rows.find((r) => r.key === selectedKey);
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
    reviewedVersion !== selected.confirmation.plan.plan_version;
  useEffect(() => {
    if (
      selected &&
      reviewedVersion === null &&
      selected.confirmation.plan?.plan_version
    )
      setReviewedVersion(selected.confirmation.plan.plan_version);
  }, [selected, reviewedVersion]);
  const save = async () => {
    setSaveError(null);
    try {
      await approvals.confirm(selected.incident, selected.id, reviewedVersion);
    } catch (error) {
      setSaveError(error.message);
    }
  };
  const teamRow = teamId ? rows.find((row) => row.id === teamId) : null;
  return (
    <>
      {teamId ? (
        <Button
          variant="outlined"
          size="small"
          onClick={() => open(teamRow)}
          aria-label={`Review plan for ${teamRow?.name || teamId}`}
        >
          {teamRow?.confirmation.plan?.approval
            ? "Confirmed · view"
            : teamRow?.confirmation.plan?.can_confirm
              ? "Review & confirm"
              : "Review plan"}
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
        open={!!selected}
        title={selected ? `Review crew plan · ${selected.name}` : ""}
        onClose={() => setSelectedKey(null)}
      >
        {selected && (
          <>
            {pending(selected) && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                <strong>Fire analyst confirmation required</strong>
                <br />
                Review the destinations, timing and prerequisites, then confirm
                this crew’s proposed plan.
              </Alert>
            )}
            {selected.confirmation.plan?.approval && (
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
                This plan changed since you opened it. Review the updated steps
                before confirming.
                <Button
                  onClick={() => {
                    setReviewedVersion(selected.confirmation.plan.plan_version);
                    setSaveError(null);
                  }}
                >
                  Review updated plan
                </Button>
              </Alert>
            )}
            <Typography color="text.secondary" sx={{ mb: 2 }}>
              {selected.incident.name}
            </Typography>
            <Typography sx={{ mb: 2 }}>
              Timing: {crewUrgency(selected.tasks).label}. Margin compares the
              supplied deadline with the planned finish.
            </Typography>
            {selected.tasks.map((step, index) => {
              const location = selected.incident.assets.find(
                (a) => a.asset_id === step.asset_id,
              );
              return (
                <div className="call-record" key={step.action_id || index}>
                  <Typography variant="h5">
                    {index + 1}.{" "}
                    {location?.name ||
                      step.asset_id ||
                      "Destination not supplied"}
                  </Typography>
                  <Coordinates location={location} />
                  <Facts
                    rows={[
                      ["Status", humanize(step.status)],
                      ["Action", humanize(step.action || step.action_id)],
                      ["Start", `${count(step.start_min)} min`],
                      ["Finish", `${count(step.finish_min)} min`],
                      [
                        "Deadline",
                        step.deadline_min == null
                          ? "Not supplied"
                          : `${count(step.deadline_min)} min`,
                      ],
                      [
                        "Prerequisites",
                        step.prerequisites?.map(humanize).join(" · ") ||
                          "None supplied",
                      ],
                    ]}
                  />
                </div>
              );
            })}
            {!selected.tasks.length && (
              <Empty title="No plan supplied for this crew" />
            )}
            {pending(selected) && (
              <div className="crew-confirmation-action">
                <Typography variant="h5">Awaiting confirmation</Typography>
                <Typography color="text.secondary" sx={{ mt: 1, mb: 2 }}>
                  {selected.confirmation.phase === "loading"
                    ? "Checking saved confirmations…"
                    : selected.confirmation.phase === "error"
                      ? selected.confirmation.error
                      : !selected.confirmation.plan?.can_confirm
                        ? "A crew identifier and current proposed plan are needed before confirmation is available."
                        : `Confirm this crew plan as ${selected.confirmation.analyst}. This records approval; dispatch remains separate.`}
                </Typography>
                {saveError && (
                  <Alert severity="error" sx={{ mb: 2 }}>
                    {saveError}
                  </Alert>
                )}
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
                <Button
                  variant="contained"
                  disabled={
                    selected.confirmation.phase !== "ready" ||
                    !selected.confirmation.plan?.can_confirm ||
                    selected.confirmation.saving ||
                    !!changed ||
                    !reviewedVersion
                  }
                  onClick={save}
                >
                  {selected.confirmation.saving
                    ? "Saving confirmation…"
                    : "Confirm crew plan"}
                </Button>
              </div>
            )}
          </>
        )}
      </DetailDialog>
    </>
  );
}
