import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Button,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Alert,
} from "@mui/material";
import {
  MainCard,
  Status,
  Empty,
  DetailDialog,
  Facts,
  PageHeading,
  Metric,
} from "../components/Common";
import IncidentMap from "../components/IncidentMap";
import {
  callRows,
  count,
  stamp,
  humanize,
  responseTeams,
  evacuationTotals,
} from "../state/model.mjs";
const fact = (value) =>
  value === true ? "Yes" : value === false ? "No" : "Unknown";
export function Calls({ incident }) {
  const [params, setParams] = useSearchParams(),
    filter = params.get("filter") || "all",
    rows = callRows(incident),
    [selected, setSelected] = useState(null);
  const filtered = rows.filter((r) =>
    filter === "pending"
      ? r.toCall
      : filter === "called"
        ? r.called
        : filter === "human"
          ? r.followup
          : true,
  );
  return (
    <>
      <PageHeading
        title="Calls & follow-up"
        description="Contact order comes from the remaining evacuation window. Counts are unique locations; repeated attempts stay together."
      />
      <div className="queue-filters">
        {[
          ["all", "All locations", rows.length],
          ["pending", "To call", rows.filter((r) => r.toCall).length],
          ["called", "Already called", rows.filter((r) => r.called).length],
          ["human", "Human follow-up", rows.filter((r) => r.followup).length],
        ].map(([id, label, total]) => (
          <Button
            key={id}
            className="queue-filter"
            variant={filter === id ? "contained" : "outlined"}
            onClick={() => setParams({ filter: id })}
          >
            <span>{label}</span>
            <strong>{total}</strong>
          </Button>
        ))}
      </div>
      <MainCard title="Contact work queue" content={false}>
        <div className="table-scroll">
          <Table>
            <TableHead>
              <TableRow>
                {[
                  "Priority",
                  "Location",
                  "Remaining window",
                  "Last call",
                  "Follow-up reasons",
                  "Details",
                ].map((c) => (
                  <TableCell key={c}>{c}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((r) => (
                <TableRow key={r.asset_id}>
                  <TableCell>
                    <span className="rank">{r.rank ?? "?"}</span>
                  </TableCell>
                  <TableCell>
                    <strong>{r.name}</strong>
                    <div className="small-muted">{humanize(r.asset_type)}</div>
                  </TableCell>
                  <TableCell>{count(r.slack_min)} min</TableCell>
                  <TableCell>
                    <Status value={r.latest?.status || "not_called"} />
                    <div className="small-muted">
                      {r.calls.length} attempt{r.calls.length !== 1 ? "s" : ""}
                    </div>
                  </TableCell>
                  <TableCell>
                    {r.reasons.length
                      ? r.reasons.map((reason) => (
                          <div className="reason" key={reason}>
                            {reason}
                          </div>
                        ))
                      : "None reported"}
                  </TableCell>
                  <TableCell>
                    <Button
                      size="small"
                      onClick={() => setSelected(r)}
                      aria-label={`View call details for ${r.name}`}
                    >
                      View details
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        {!filtered.length && <Empty title="No locations in this queue" />}
      </MainCard>
      <Typography variant="body2" color="text.secondary">
        Already called means a completed call record exists, not that
        instructions were understood. Human follow-up may overlap with completed
        calls. This page does not place calls.
      </Typography>
      <DetailDialog
        open={!!selected}
        title={selected ? `Call details · ${selected.name}` : ""}
        onClose={() => setSelected(null)}
      >
        {selected && (
          <>
            <Typography variant="h5">Human follow-up reasons</Typography>
            {selected.reasons.length ? (
              <ul>
                {selected.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : (
              <p>No human follow-up reason supplied.</p>
            )}
            <Typography variant="h5" sx={{ mt: 3 }}>
              Call history
            </Typography>
            {selected.calls.length ? (
              selected.calls.map((c) => (
                <div className="call-record" key={c.request_id}>
                  <Facts
                    rows={[
                      ["Request", c.request_id],
                      ["Status", humanize(c.status)],
                      ["Observed at", stamp(c.observed_at)],
                      ["Wants a human", fact(c.wants_human)],
                      [
                        "Assistance requested",
                        fact(c.reported_needs_assistance ?? c.needs_assistance),
                      ],
                      [
                        "Instructions acknowledged",
                        fact(c.message_acknowledged ?? c.acknowledged),
                      ],
                      ["Can self-evacuate", fact(c.can_self_evacuate)],
                      ["Departure confirmed", fact(c.departure_confirmed)],
                      ["Arrival confirmed", fact(c.arrival_confirmed)],
                      ["Source", c.source || c.provenance?.source],
                    ]}
                  />
                </div>
              ))
            ) : (
              <Empty title="No call records" />
            )}
          </>
        )}
      </DetailDialog>
    </>
  );
}
export function ResponsePlan({ incident }) {
  const teams = responseTeams(incident),
    response = incident.plan.response;
  return (
    <>
      <PageHeading
        title="Firefighter plan"
        description="Team-by-team steps, destinations and dependencies, in the order supplied by coordination."
      />
      <Alert severity="info">
        Response proposals require separate dispatch confirmation. Travel and
        action times are minutes from the plan’s reference time.
      </Alert>
      {!response ? (
        <MainCard>
          <Empty title="No response plan supplied">
            A plan will appear when coordination provides current crews, routes
            and feasible actions.
          </Empty>
        </MainCard>
      ) : (
        <>
          <div className="plan-layout">
            <div className="plan-teams">
              {teams.map((team) => (
                <MainCard
                  title={
                    incident.teams.find((t) => t.team_id === team.team_id)
                      ?.name || team.team_id
                  }
                  action={<Status value="proposed" />}
                  key={team.team_id}
                >
                  {(team.tasks || []).length ? (
                    (team.tasks || []).map((step, index) => {
                      const asset = incident.assets.find(
                        (a) => a.asset_id === step.asset_id,
                      );
                      return (
                        <div
                          className="plan-step"
                          key={step.action_id || index}
                        >
                          <div className="step-number">{index + 1}</div>
                          <div className="step-body">
                            <div className="step-title">
                              <strong>
                                {asset?.name ||
                                  step.asset_id ||
                                  "Destination not supplied"}
                              </strong>
                              <Status value={step.status || "proposed"} />
                            </div>
                            <Typography color="text.secondary">
                              {humanize(step.action || step.action_id)}
                            </Typography>
                            <div className="timing-strip">
                              <span>
                                Depart <b>{count(step.depart_min)} min</b>
                              </span>
                              <span>
                                Start <b>{count(step.start_min)} min</b>
                              </span>
                              <span>
                                Finish <b>{count(step.finish_min)} min</b>
                              </span>
                            </div>
                            {step.prerequisites?.length > 0 && (
                              <div className="dependency">
                                <strong>Prerequisites</strong>
                                {step.prerequisites.map((p) => (
                                  <div key={p}>{humanize(p)}</div>
                                ))}
                              </div>
                            )}
                            <div className="small-muted">
                              Route: {step.route_source || "Not supplied"}
                            </div>
                          </div>
                        </div>
                      );
                    })
                  ) : (
                    <Empty title="No steps assigned" />
                  )}
                </MainCard>
              ))}
            </div>
            <MainCard title="Proposed routes" content={false}>
              <IncidentMap incidents={[incident]} showRoutes />
            </MainCard>
          </div>
          <MainCard title="Blockers & uncovered locations">
            {Object.entries(response.unserved || {}).map(([id, reason]) => (
              <div className="blocked-item" key={id}>
                <strong>
                  {incident.assets.find((a) => a.asset_id === id)?.name || id}
                </strong>
                <p>
                  {Array.isArray(reason)
                    ? reason.map(humanize).join(", ")
                    : humanize(reason)}
                </p>
              </div>
            ))}
            {Object.entries(response.blocked_actions || {}).map(
              ([id, reasons]) => (
                <p key={id}>
                  {id}: {reasons.map(humanize).join(", ")}
                </p>
              ),
            )}
            {[...(response.unassigned || []), ...(response.review || [])].map(
              (r, n) => (
                <p key={n}>
                  {r.asset_id || r.action_id}:{" "}
                  {r.reason || r.reasons?.map(humanize).join(", ")}
                </p>
              ),
            )}
            {!Object.keys(response.unserved || {}).length &&
              !Object.keys(response.blocked_actions || {}).length &&
              !response.unassigned?.length &&
              !response.review?.length && (
                <Typography color="text.secondary">
                  No blockers supplied.
                </Typography>
              )}
            {(response.assumptions || []).map((a, n) => (
              <Typography color="text.secondary" sx={{ mt: 1 }} key={n}>
                {a}
              </Typography>
            ))}
          </MainCard>
        </>
      )}
    </>
  );
}
export function Evacuation({ incident, incidents }) {
  const scope = incident ? [incident] : incidents,
    groups = scope.flatMap((i) =>
      (i.peopleClusters || []).map((g) => ({ ...g, incident_name: i.name })),
    ),
    totals = incident
      ? evacuationTotals(incident)
      : evacuationTotals({
          peopleClusters: scope.every((i) => i.peopleClusters) ? groups : null,
        });
  return (
    <>
      <PageHeading
        title="Evacuation & people"
        description="People counts and group progress. Ability, departure and arrival are separate facts."
      />
      <div className="metric-grid">
        <Metric
          label="People self-evacuating"
          value={totals.selfEvacuating}
          note="Departure reported; arrival pending"
        />
        <Metric
          label="People needing assistance"
          value={totals.assistance}
          note="Requires assisted evacuation"
        />
        <Metric
          label="People awaiting assessment"
          value={totals.unknown}
          note="Ability or progress not confirmed"
        />
        <Metric
          label="People arrived"
          value={totals.arrived}
          note="Arrival reported"
        />
      </div>
      {groups.length ? (
        <MainCard
          title={`Identified people clusters (${groups.length})`}
          content={false}
        >
          <div className="table-scroll">
            <Table>
              <TableHead>
                <TableRow>
                  {[
                    "Group / location",
                    "Incident",
                    "People",
                    "Progress",
                    "Updated",
                    "Source",
                  ].map((c) => (
                    <TableCell key={c}>{c}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {groups.map((g) => (
                  <TableRow key={g.id}>
                    <TableCell>{g.name}</TableCell>
                    <TableCell>{g.incident_name}</TableCell>
                    <TableCell>{count(g.people)}</TableCell>
                    <TableCell>
                      <Status value={g.status} />
                    </TableCell>
                    <TableCell>{stamp(g.updated_at)}</TableCell>
                    <TableCell>{g.source}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </MainCard>
      ) : (
        <Alert severity="info">
          A population-group registry and confirmed group headcounts have not
          been supplied. Building occupancy is an estimate and is not used as an
          evacuation count.
        </Alert>
      )}
      <MainCard title="Location readiness" content={false}>
        <div className="table-scroll">
          <Table>
            <TableHead>
              <TableRow>
                {[
                  "Location",
                  "Estimated occupancy",
                  "Reported ability",
                  "Assistance requested",
                  "Departure",
                  "Arrival",
                  "Destination",
                ].map((c) => (
                  <TableCell key={c}>{c}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {scope.flatMap((i) =>
                callRows(i).map((r) => {
                  const p = i.plan.locations?.find(
                    (l) => l.asset_id === r.asset_id,
                  );
                  return (
                    <TableRow key={`${i.id}:${r.asset_id}`}>
                      <TableCell>{r.name}</TableCell>
                      <TableCell>{count(r.estimated_occupancy)}</TableCell>
                      <TableCell>{fact(r.latest?.can_self_evacuate)}</TableCell>
                      <TableCell>
                        {fact(
                          r.latest?.reported_needs_assistance ??
                            r.latest?.needs_assistance,
                        )}
                      </TableCell>
                      <TableCell>
                        {fact(r.latest?.departure_confirmed)}
                      </TableCell>
                      <TableCell>{fact(r.latest?.arrival_confirmed)}</TableCell>
                      <TableCell>
                        {p?.destination_name || "Not supplied"}
                      </TableCell>
                    </TableRow>
                  );
                }),
              )}
            </TableBody>
          </Table>
        </div>
      </MainCard>
    </>
  );
}
export function Resources({ incidents }) {
  const rows = incidents.flatMap((i) =>
    (
      i.resources ||
      i.teams.map((t) => ({
        id: t.team_id,
        name: t.name,
        type: t.capabilities?.join(", "),
        status: null,
        available: t.available,
        source: t.source,
      }))
    ).map((r) => ({ ...r, incident_name: i.name, incident_id: i.id })),
  );
  return (
    <>
      <PageHeading
        title="Resources"
        description="Crews, vehicles and aircraft associated with the connected incidents."
      />
      <MainCard title={`Resource records (${rows.length})`} content={false}>
        <div className="table-scroll">
          <Table>
            <TableHead>
              <TableRow>
                {[
                  "Resource",
                  "Type / capabilities",
                  "Incident",
                  "Deployment",
                  "Availability",
                  "Source",
                ].map((c) => (
                  <TableCell key={c}>{c}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={`${r.incident_id}:${r.id}`}>
                  <TableCell>
                    <strong>{r.name}</strong>
                  </TableCell>
                  <TableCell>{r.type || "Not supplied"}</TableCell>
                  <TableCell>{r.incident_name}</TableCell>
                  <TableCell>
                    <Status value={r.status} />
                  </TableCell>
                  <TableCell>{fact(r.available)}</TableCell>
                  <TableCell>{r.source || "Not supplied"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        {!rows.length && (
          <Empty title="No resources supplied">
            Current crews, vehicles and aircraft will appear here when a
            resource roster is connected.
          </Empty>
        )}
      </MainCard>
      <Typography variant="body2" color="text.secondary">
        Unavailable does not mean deployed. A proposed assignment does not
        confirm mobilisation or movement.
      </Typography>
    </>
  );
}
