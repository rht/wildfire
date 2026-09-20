import { useState } from "react";
import { Button, Typography, Alert, Chip } from "@mui/material";
import { MainCard, Empty, DetailDialog, Coordinates, Facts } from "./Common";
import {
  responseTeams,
  humanize,
  count,
  crewUrgency,
} from "../state/model.mjs";

const needsConfirmation = (tasks) =>
  tasks.some((step) => step.status === "proposed");

export default function CrewPlans({ incidents, teamId }) {
  const [selectedKey, setSelectedKey] = useState(null);
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
  const selected = rows.find((r) => r.key === selectedKey);
  const awaiting = rows.filter((row) => needsConfirmation(row.tasks)).length;
  const teamRow = teamId ? rows.find((row) => row.id === teamId) : null;
  return (
    <>
      {teamId ? (
        <Button
          variant="outlined"
          size="small"
          onClick={() => setSelectedKey(teamRow?.key)}
          aria-label={`Review plan for ${teamRow?.name || teamId}`}
        >
          {needsConfirmation(teamRow?.tasks || [])
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
                onClick={() => setSelectedKey(row.key)}
                aria-label={`Review plan for ${row.name}`}
              >
                <span className="crew-identity">
                  <strong>{row.name}</strong>
                  <small>{row.incident.name}</small>
                  {needsConfirmation(row.tasks) && (
                    <small className="confirmation-pending">
                      Awaiting analyst confirmation
                    </small>
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
                    ? needsConfirmation(row.tasks)
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
            {needsConfirmation(selected.tasks) && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                <strong>Fire analyst confirmation required</strong>
                <br />
                Review the destinations, timing and prerequisites, then confirm
                this crew’s proposed plan.
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
            {needsConfirmation(selected.tasks) && (
              <div className="crew-confirmation-action">
                <Typography variant="h5">
                  Awaiting analyst confirmation
                </Typography>
                <Typography color="text.secondary" sx={{ mt: 1, mb: 2 }}>
                  Confirmation cannot be saved yet because the approval service
                  is not connected. Reviewing the plan does not confirm it or
                  dispatch the crew.
                </Typography>
                <Button variant="contained" disabled>
                  Confirm crew plan
                </Button>
              </div>
            )}
          </>
        )}
      </DetailDialog>
    </>
  );
}
