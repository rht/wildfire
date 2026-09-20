import { useState } from "react";
import { Button, Typography, Alert, Chip } from "@mui/material";
import { MainCard, Empty, DetailDialog, Coordinates, Facts } from "./Common";
import {
  responseTeams,
  humanize,
  count,
  crewUrgency,
} from "../state/model.mjs";

export default function CrewPlans({ incidents }) {
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
  return (
    <>
      <MainCard
        title="Crew plans"
        action={<span className="small-muted">{rows.length} crews</span>}
      >
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
                  ? `${row.tasks.length} steps · Review`
                  : "No plan supplied"}
              </span>
            </Button>
          ))}
        </div>
        {!rows.length && <Empty title="No crews supplied" />}
      </MainCard>
      <DetailDialog
        open={!!selected}
        title={selected ? `Review crew plan · ${selected.name}` : ""}
        onClose={() => setSelectedKey(null)}
      >
        {selected && (
          <>
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
            <Alert severity="info" sx={{ my: 2 }}>
              Analyst confirmation is not connected yet. Reviewing this plan
              does not dispatch a crew.
            </Alert>
            <Button variant="contained" disabled>
              Confirm plan
            </Button>
          </>
        )}
      </DetailDialog>
    </>
  );
}
