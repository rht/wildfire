import { useEffect, useState } from "react";
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
  Coordinates,
  FilterSelect,
  FilterInput,
  ClearFilters,
  MainCard,
  Status,
  Empty,
  DetailDialog,
  Facts,
  PageHeading,
  Metric,
} from "../components/Common";
import CrewPlans from "../components/CrewPlans";
import IncidentMap from "../components/IncidentMap";
import {
  gps,
  callActor,
  callHistory,
  filterCallHistory,
  callRows,
  count,
  stamp,
  humanize,
  responseTeams,
  evacuationTotals,
  readinessFacts,
} from "../state/model.mjs";
const callerLabel = (value) =>
  ({ agent: "Voice assistant", human: "Human", unknown: "Not supplied" })[
    value
  ];
const callDefaults = { search: "", caller: "", outcome: "", from: "", to: "" };
const fact = (value) =>
  value === true ? "Yes" : value === false ? "No" : "Unknown";
export function Calls({ incident }) {
  const [params, setParams] = useSearchParams(),
    filter = params.get("filter") || "all",
    rows = callRows(incident);
  const [filters, setFilters] = useState(callDefaults);
  const set = (key, value) => setFilters((f) => ({ ...f, [key]: value }));
  const [selectedAssetId, setSelectedAssetId] = useState(null);
  const selected = rows.find((row) => row.asset_id === selectedAssetId) || null;
  useEffect(() => {
    if (selectedAssetId && !selected) setSelectedAssetId(null);
  }, [selected, selectedAssetId]);
  const filtered = rows.filter((r) =>
    filter === "pending"
      ? r.toCall
      : filter === "called"
        ? r.attempted
        : filter === "completed"
          ? r.completed
          : filter === "human"
            ? r.followup
            : true,
  );
  const history = filterCallHistory(callHistory(filtered), filters);
  const pending = filtered.filter(
    (r) =>
      r.toCall &&
      (!filters.search ||
        `${r.name} ${r.asset_id}`
          .toLowerCase()
          .includes(filters.search.toLowerCase())),
  );
  return (
    <>
      <PageHeading
        title="Calls & follow-up"
        description="Counts are unique locations. Call history shows individual attempts, newest first."
      />
      <div className="queue-filters">
        {[
          ["all", "All locations", rows.length],
          [
            "pending",
            "Voice assistant to call",
            rows.filter((r) => r.toCall).length,
          ],
          ["called", "Call attempted", rows.filter((r) => r.attempted).length],
          [
            "completed",
            "Call completed",
            rows.filter((r) => r.completed).length,
          ],
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
      <MainCard>
        <div className="filters">
          <FilterInput
            label="Search calls"
            value={filters.search}
            onChange={(v) => set("search", v)}
            placeholder="Location or request ID"
          />
          {filter !== "pending" && (
            <>
              <FilterSelect
                label="Caller"
                value={filters.caller}
                onChange={(v) => set("caller", v)}
                options={[
                  ["", "All callers"],
                  ["agent", "Voice assistant"],
                  ["human", "Human"],
                  ["unknown", "Not supplied"],
                ]}
              />
              <FilterSelect
                label="Call outcome"
                value={filters.outcome}
                onChange={(v) => set("outcome", v)}
                options={[
                  ["", "All outcomes"],
                  ...[
                    ...new Set(
                      incident.calls.map((c) => c.status).filter(Boolean),
                    ),
                  ].map((v) => [v, humanize(v)]),
                ]}
              />
              <FilterInput
                label="Call from"
                type="date"
                value={filters.from}
                onChange={(v) => set("from", v)}
              />
              <FilterInput
                label="Call to"
                type="date"
                value={filters.to}
                onChange={(v) => set("to", v)}
              />
            </>
          )}
          <ClearFilters onClick={() => setFilters(callDefaults)} />
        </div>
      </MainCard>
      {filter !== "pending" && (
        <MainCard title="Call history" content={false}>
          <div className="table-scroll">
            <Table>
              <TableHead>
                <TableRow>
                  {[
                    "Location",
                    "Caller",
                    "Call time",
                    "Outcome",
                    "Follow-up reasons",
                    "Review reasons",
                    "Details",
                  ].map((c) => (
                    <TableCell key={c}>{c}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {history.map((r, n) => (
                  <TableRow key={`${r.asset_id}:${r.call.request_id || n}`}>
                    <TableCell>
                      <strong>{r.name}</strong>
                      <Coordinates location={r} />
                    </TableCell>
                    <TableCell>{callerLabel(r.caller)}</TableCell>
                    <TableCell className="nowrap">
                      {stamp(r.call.observed_at)}
                    </TableCell>
                    <TableCell>
                      <Status value={r.call.status} />
                      {r.call.queue_state != null && (
                        <div className="small-muted">
                          Queue: {humanize(r.call.queue_state)}
                        </div>
                      )}
                      <div className="small-muted call-request-id">
                        {r.call.request_id}
                      </div>
                    </TableCell>
                    <TableCell className="call-followup-reasons">
                      {callRows({
                        ...incident,
                        calls: [r.call],
                        plan: { locations: [] },
                      })
                        .find((row) => row.asset_id === r.asset_id)
                        ?.reasons.join(" · ") || "None reported"}
                    </TableCell>
                    <TableCell>
                      {r.reviewReasons.join(" · ") || "None supplied"}
                    </TableCell>
                    <TableCell>
                      <Button
                        size="small"
                        onClick={() => setSelectedAssetId(r.asset_id)}
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
          {!history.length && <Empty title="No matching call records" />}
        </MainCard>
      )}
      {filter === "pending" && (
        <MainCard title="Voice assistant to call" content={false}>
          <div className="table-scroll">
            <Table>
              <TableHead>
                <TableRow>
                  {[
                    "Priority",
                    "Location",
                    "Call state",
                    "Remaining window",
                    "Review reasons",
                    "Details",
                  ].map((c) => (
                    <TableCell key={c}>{c}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {pending.map((r) => (
                  <TableRow key={r.asset_id}>
                    <TableCell>{r.rank ?? "—"}</TableCell>
                    <TableCell>
                      <strong>{r.name}</strong>
                      <Coordinates location={r} />
                    </TableCell>
                    <TableCell>
                      {r.queued ? "Queued request" : "No call records"}
                    </TableCell>
                    <TableCell>{count(r.slack_min)} min</TableCell>
                    <TableCell>
                      {r.reviewReasons.join(" · ") || "None supplied"}
                    </TableCell>
                    <TableCell>
                      <Button
                        size="small"
                        onClick={() => setSelectedAssetId(r.asset_id)}
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
          {!pending.length && <Empty title="No matching locations" />}
        </MainCard>
      )}
      <Typography variant="body2" color="text.secondary">
        To call shows locations with no call records or an unstarted queued
        request. Supplied queue membership must be pending; cancelled, held and
        started requests are excluded. Without queue data, eligibility is
        unknown. Attempted includes no answer and other started or terminal
        outcomes. Completed counts completed calls only, without proving
        instructions were understood. A supplied queued retry can overlap with
        attempted calls; human follow-up can overlap with either. This page does
        not place calls.
      </Typography>
      <DetailDialog
        open={!!selected}
        title={selected ? `Call details · ${selected.name}` : ""}
        onClose={() => setSelectedAssetId(null)}
      >
        {selected && (
          <>
            <Facts rows={[["GPS (latitude, longitude)", gps(selected)]]} />
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
              Review reasons
            </Typography>
            {selected.reviewReasons.length ? (
              <ul>
                {selected.reviewReasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : (
              <p>No operational review reason supplied.</p>
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
                      ["Caller", callerLabel(callActor(c))],
                      ["Status", humanize(c.status)],
                      ["Queue state", humanize(c.queue_state)],
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
      <Alert severity="warning">
        <strong>Fire analyst confirmation required.</strong> Review and confirm
        each proposed crew plan. Approval is separate from dispatch.
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
                  action={
                    <CrewPlans incidents={[incident]} teamId={team.team_id} />
                  }
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
                            <Coordinates location={asset} />
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
  const [search, setSearch] = useState(""),
    [incidentFilter, setIncidentFilter] = useState("");
  const scope = (incident ? [incident] : incidents).filter(
      (i) => !incidentFilter || i.id === incidentFilter,
    ),
    groups = scope.flatMap((i) =>
      (i.peopleClusters || []).map((g) => ({
        ...g,
        location:
          gps(g) !== "Not supplied"
            ? g
            : i.assets.find((a) => a.asset_id === g.asset_id),
        incident_name: i.name,
      })),
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
      <MainCard>
        <div className="filters">
          <FilterInput
            label="Search people and locations"
            value={search}
            onChange={setSearch}
          />
          {!incident && (
            <FilterSelect
              label="Incident"
              value={incidentFilter}
              onChange={setIncidentFilter}
              options={[
                ["", "All incidents"],
                ...incidents.map((i) => [i.id, i.name]),
              ]}
            />
          )}
          <ClearFilters
            onClick={() => {
              setSearch("");
              setIncidentFilter("");
            }}
          />
        </div>
      </MainCard>
      {groups.length ? (
        <MainCard
          title={`Identified people / groups (${groups.length})`}
          content={false}
        >
          <div className="table-scroll">
            <Table>
              <TableHead>
                <TableRow>
                  {[
                    "Person / group / location",
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
                {groups
                  .filter(
                    (g) =>
                      !search ||
                      `${g.name} ${g.id}`
                        .toLowerCase()
                        .includes(search.toLowerCase()),
                  )
                  .map((g) => (
                    <TableRow key={g.id}>
                      <TableCell>
                        {g.name}
                        <Coordinates location={g.location} />
                      </TableCell>
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
      <MainCard title="Identified groups" content={false}>
        <div className="table-scroll">
          <Table>
            <TableHead>
              <TableRow>
                {[
                  "Location",
                  "Estimated occupancy",
                  "Reported ability",
                  "Assistance requested",
                  "Transport available",
                  "Departure",
                  "Arrival",
                  "Destination",
                  "Review / evidence",
                ].map((c) => (
                  <TableCell key={c}>{c}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {scope.flatMap((i) =>
                callRows(i)
                  .filter(
                    (r) =>
                      !search ||
                      `${r.name} ${r.asset_id}`
                        .toLowerCase()
                        .includes(search.toLowerCase()),
                  )
                  .map((r) => {
                    const p = i.plan.locations?.find(
                        (l) => l.asset_id === r.asset_id,
                      ),
                      readiness = readinessFacts(i, r);
                    return (
                      <TableRow key={`${i.id}:${r.asset_id}`}>
                        <TableCell>
                          {r.name}
                          <Coordinates location={r} />
                        </TableCell>
                        <TableCell>{count(r.estimated_occupancy)}</TableCell>
                        <TableCell>{fact(readiness.canSelfEvacuate)}</TableCell>
                        <TableCell>{fact(readiness.needsAssistance)}</TableCell>
                        <TableCell>
                          {fact(readiness.transportAvailable)}
                        </TableCell>
                        <TableCell>
                          {fact(readiness.departureConfirmed)}
                        </TableCell>
                        <TableCell>
                          {fact(readiness.arrivalConfirmed)}
                        </TableCell>
                        <TableCell>
                          {p?.destination_name || "Not supplied"}
                          {p?.destination_name && (
                            <Coordinates
                              location={i.assets.find(
                                (a) =>
                                  a.asset_id === p.destination_id ||
                                  a.asset_id === p.centre_id,
                              )}
                            />
                          )}
                        </TableCell>
                        <TableCell>
                          {readiness.assistanceReviewRequired && (
                            <div className="reason">
                              Assistance review pending
                            </div>
                          )}
                          {readiness.conflicts.length > 0 && (
                            <div className="reason">
                              Conflicting reports:{" "}
                              {readiness.conflicts.map(humanize).join(", ")}
                            </div>
                          )}
                          <div className="small-muted">
                            Evidence: {readiness.source || "Not supplied"}
                          </div>
                          {readiness.requestIds.length > 0 && (
                            <div className="small-muted">
                              Requests: {readiness.requestIds.join(", ")}
                            </div>
                          )}
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
  const [search, setSearch] = useState(""),
    [type, setType] = useState(""),
    [deployment, setDeployment] = useState(""),
    [incidentFilter, setIncidentFilter] = useState("");
  const rows = incidents.flatMap((i) =>
    (
      i.resources ||
      i.teams.map((t) => ({
        ...t,
        id: t.team_id,
        name: t.name,
        type: t.capabilities?.join(", "),
        status: null,
        available: t.available,
        source: t.source,
      }))
    ).map((r) => ({
      ...r,
      incident: i,
      incident_name: i.name,
      incident_id: i.id,
      plannedTeam: responseTeams(i).find(
        (team) => team.team_id === (r.team_id || r.id),
      ),
    })),
  );
  return (
    <>
      <PageHeading
        title="Resources"
        description="Crews, vehicles and aircraft associated with the connected incidents."
      />
      <MainCard>
        <div className="filters">
          <FilterInput
            label="Search resources"
            value={search}
            onChange={setSearch}
          />
          <FilterSelect
            label="Resource type"
            value={type}
            onChange={setType}
            options={[
              ["", "All types"],
              ...[...new Set(rows.map((r) => r.type).filter(Boolean))].map(
                (v) => [v, v],
              ),
            ]}
          />
          <FilterSelect
            label="Deployment"
            value={deployment}
            onChange={setDeployment}
            options={[
              ["", "All deployments"],
              ...[...new Set(rows.map((r) => r.status || "unknown"))].map(
                (v) => [v, humanize(v)],
              ),
            ]}
          />
          {incidents.length > 1 && (
            <FilterSelect
              label="Incident"
              value={incidentFilter}
              onChange={setIncidentFilter}
              options={[
                ["", "All incidents"],
                ...incidents.map((i) => [i.id, i.name]),
              ]}
            />
          )}
          <ClearFilters
            onClick={() => {
              setSearch("");
              setType("");
              setDeployment("");
              setIncidentFilter("");
            }}
          />
        </div>
      </MainCard>
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
                  "Plan",
                  "Availability",
                  "Source",
                ].map((c) => (
                  <TableCell key={c}>{c}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows
                .filter(
                  (r) =>
                    (!search ||
                      `${r.name} ${r.id}`
                        .toLowerCase()
                        .includes(search.toLowerCase())) &&
                    (!type || r.type === type) &&
                    (!deployment || (r.status || "unknown") === deployment) &&
                    (!incidentFilter || r.incident_id === incidentFilter),
                )
                .map((r) => (
                  <TableRow key={`${r.incident_id}:${r.id}`}>
                    <TableCell>
                      <strong>{r.name}</strong>
                      <Coordinates location={r} />
                    </TableCell>
                    <TableCell>{r.type || "Not supplied"}</TableCell>
                    <TableCell>{r.incident_name}</TableCell>
                    <TableCell>
                      <Status value={r.status} />
                    </TableCell>
                    <TableCell>
                      {r.plannedTeam?.tasks?.length ? (
                        <CrewPlans
                          incidents={[r.incident]}
                          teamId={r.plannedTeam.team_id}
                        />
                      ) : (
                        <span className="small-muted">No plan supplied</span>
                      )}
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
