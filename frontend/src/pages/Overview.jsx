import {
  Button,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
} from "@mui/material";
import { Link } from "react-router-dom";
import FireOutlined from "@ant-design/icons/FireOutlined";
import TeamOutlined from "@ant-design/icons/TeamOutlined";
import BankOutlined from "@ant-design/icons/BankOutlined";
import UsergroupAddOutlined from "@ant-design/icons/UsergroupAddOutlined";
import {
  MainCard,
  Metric,
  Status,
  Empty,
  PageHeading,
} from "../components/Common";
import IncidentMap from "../components/IncidentMap";
import { metrics, callRows, count, stamp } from "../state/model.mjs";
export function Overview({ incidents, incident }) {
  const scope = incident ? [incident] : incidents,
    totals = metrics(scope);
  const prefix = incident
    ? `/incidents/${encodeURIComponent(incident.id)}`
    : "";
  const urgent = scope
    .flatMap((i) =>
      callRows(i)
        .filter((r) => r.followup)
        .map((r) => ({ ...r, incident: i })),
    )
    .slice(0, 4);
  return (
    <>
      <PageHeading
        title={incident ? "Incident summary" : "Operations overview"}
        description={
          incident
            ? incident.area || "Incident status and coordination priorities"
            : "A shared view of incidents, resources and the people who need a response."
        }
      />
      <div className="metric-grid">
        <Metric
          label="Active incidents"
          value={totals.active}
          note={
            totals.active === null
              ? `${scope.length} incident source connected · status not supplied`
              : "Fires in the current operational picture"
          }
          to="/incidents"
          icon={FireOutlined}
          tone="red"
          testId="metric-active"
        />
        <Metric
          label="Deployed resources"
          value={totals.deployed}
          note={
            totals.deployed === null
              ? "Deployment status not supplied"
              : "Crews, fire engines and aircraft"
          }
          to="/resources"
          icon={TeamOutlined}
          tone="blue"
          testId="metric-deployed"
        />
        <Metric
          label="Structures / points of interest"
          value={totals.structures}
          note="Identified locations in the current scope"
          to={`${prefix}/buildings`}
          icon={BankOutlined}
          tone="orange"
        />
        <Metric
          label="People clusters"
          value={totals.clusters}
          note={
            totals.clusters === null
              ? "Group registry not supplied"
              : "Identified groups · not individual people"
          }
          to={incident ? `${prefix}/evacuation` : "/people"}
          icon={UsergroupAddOutlined}
          tone="green"
        />
      </div>
      <div className="overview-grid">
        <MainCard
          title="Incident map"
          action={
            <span className="small-muted">Select a location to inspect</span>
          }
          content={false}
        >
          <IncidentMap incidents={scope} />
        </MainCard>
        <MainCard
          title="Needs attention"
          action={<span className="attention-count">{urgent.length}</span>}
        >
          <Typography color="text.secondary" sx={{ mb: 2 }}>
            Human follow-ups and assistance requests
          </Typography>
          {urgent.length ? (
            urgent.map((r) => (
              <Link
                className="attention-item"
                key={`${r.incident.id}:${r.asset_id}`}
                to={`/incidents/${encodeURIComponent(r.incident.id)}/calls?filter=human`}
              >
                <div className="attention-icon">!</div>
                <div>
                  <strong>{r.name}</strong>
                  <p>{r.reasons.join(" · ")}</p>
                  <small>{r.incident.name}</small>
                </div>
              </Link>
            ))
          ) : (
            <Empty title="No follow-ups supplied">
              Pending and unknown contacts remain in each incident’s call queue.
            </Empty>
          )}
          <Button
            component={Link}
            to={incident ? `${prefix}/calls` : "/incidents"}
            fullWidth
            variant="outlined"
            sx={{ mt: 2 }}
          >
            Open incident work queues
          </Button>
        </MainCard>
      </div>
      <MainCard
        title={incident ? "Contact priorities" : "Incident overview"}
        action={
          <Button
            component={Link}
            to={incident ? `${prefix}/calls` : "/incidents"}
            size="small"
          >
            View all
          </Button>
        }
        content={false}
      >
        <div className="table-scroll">
          <Table size="small">
            <TableHead>
              <TableRow>
                {(incident
                  ? [
                      "Location",
                      "Contact rank",
                      "Remaining window",
                      "Call state",
                      "Human follow-up",
                    ]
                  : [
                      "Incident",
                      "Status",
                      "Identified locations",
                      "Calls pending",
                      "Human follow-up",
                      "Last assessment",
                    ]
                ).map((x) => (
                  <TableCell key={x}>{x}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {incident
                ? callRows(incident)
                    .slice(0, 5)
                    .map((r) => (
                      <TableRow key={r.asset_id}>
                        <TableCell>
                          <Link
                            to={`${prefix}/buildings?building=${encodeURIComponent(r.asset_id)}`}
                          >
                            {r.name}
                          </Link>
                        </TableCell>
                        <TableCell>{r.rank ?? "Needs review"}</TableCell>
                        <TableCell>{count(r.slack_min)} min</TableCell>
                        <TableCell>
                          <Status value={r.latest?.status || "not_called"} />
                        </TableCell>
                        <TableCell>
                          {r.followup ? "Required" : "None reported"}
                        </TableCell>
                      </TableRow>
                    ))
                : scope.map((i) => (
                    <TableRow key={i.id}>
                      <TableCell>
                        <Link
                          to={`/incidents/${encodeURIComponent(i.id)}/summary`}
                        >
                          {i.name}
                        </Link>
                        <div className="small-muted">{i.area || i.id}</div>
                      </TableCell>
                      <TableCell>
                        <Status value={i.status} />
                      </TableCell>
                      <TableCell>{i.assets.length}</TableCell>
                      <TableCell>
                        {callRows(i).filter((r) => r.toCall).length}
                      </TableCell>
                      <TableCell>
                        {callRows(i).filter((r) => r.followup).length}
                      </TableCell>
                      <TableCell>{stamp(i.as_of)}</TableCell>
                    </TableRow>
                  ))}
            </TableBody>
          </Table>
        </div>
      </MainCard>
    </>
  );
}
export function Incidents({ incidents }) {
  return (
    <>
      <PageHeading
        title="Incidents"
        description="Select a fire to open its calls, response plan, evacuation and risk assessments."
      />
      <div className="incident-cards">
        {incidents.map((i) => (
          <MainCard
            key={i.id}
            title={i.name}
            action={<Status value={i.status} />}
          >
            <Typography color="text.secondary">{i.area || i.id}</Typography>
            <div className="incident-card-stats">
              <div>
                <strong>{i.assets.length}</strong>Locations
              </div>
              <div>
                <strong>{callRows(i).filter((c) => c.toCall).length}</strong>To
                call
              </div>
              <div>
                <strong>{callRows(i).filter((c) => c.followup).length}</strong>
                Human follow-up
              </div>
            </div>
            <Typography variant="body2" color="text.secondary">
              Assessment: {stamp(i.as_of)}
            </Typography>
            <Button
              component={Link}
              to={`/incidents/${encodeURIComponent(i.id)}/summary`}
              variant="outlined"
              sx={{ mt: 2 }}
            >
              Open incident
            </Button>
          </MainCard>
        ))}
      </div>
      {!incidents.length && <Empty title="Waiting for incident data" />}
    </>
  );
}
