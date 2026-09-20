import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
} from "@mui/material";
import { useState } from "react";
import { Link } from "react-router-dom";
import FireOutlined from "@ant-design/icons/FireOutlined";
import TeamOutlined from "@ant-design/icons/TeamOutlined";
import BankOutlined from "@ant-design/icons/BankOutlined";
import UsergroupAddOutlined from "@ant-design/icons/UsergroupAddOutlined";
import {
  FilterInput,
  FilterSelect,
  ClearFilters,
  MainCard,
  Metric,
  Status,
  Empty,
  PageHeading,
} from "../components/Common";
import CrewPlans from "../components/CrewPlans";
import IncidentMap from "../components/IncidentMap";
import { metrics, callRows, stamp, gps, incidentGps } from "../state/model.mjs";
export function Overview({ incidents, incident }) {
  const scope = incident ? [incident] : incidents,
    totals = metrics(scope),
    coordinates = incident ? incidentGps(incident) : null;
  const prefix = incident
    ? `/incidents/${encodeURIComponent(incident.id)}`
    : "";
  return (
    <>
      {!incident && <PageHeading title="Operations overview" />}
      <div className="metric-grid">
        {incident && (
          <Metric
            label="GPS coordinates"
            valueText={gps(coordinates.point)}
            note={coordinates.basis}
            testId="metric-gps"
          />
        )}
        {!incident && (
          <Metric
            compact
            label="Incidents"
            value={scope.length}
            to="/incidents"
            icon={FireOutlined}
            tone="red"
            testId="metric-active"
          />
        )}
        <Metric
          compact
          label="Deployed resources"
          value={totals.deployed}
          to={`${prefix}/resources`}
          icon={TeamOutlined}
          tone="blue"
          testId="metric-deployed"
        />
        <Metric
          compact
          label="Structures / points of interest"
          value={totals.structures}
          to={`${prefix}/buildings`}
          icon={BankOutlined}
          tone="orange"
          testId="metric-structures"
        />
        <Metric
          compact
          label="People / groups"
          value={totals.clusters}
          to={incident ? `${prefix}/evacuation` : "/people"}
          icon={UsergroupAddOutlined}
          tone="green"
          testId="metric-people"
        />
      </div>
      <div className="overview-grid">
        <MainCard
          title={incident ? "Incident area" : "Incidents map"}
          content={false}
        >
          <IncidentMap incidents={scope} openIncidentOnClick={!incident} />
        </MainCard>
        <CrewPlans incidents={scope} />
      </div>
    </>
  );
}
export function Incidents({ incidents }) {
  const [search, setSearch] = useState(""),
    [status, setStatus] = useState("");
  const filtered = incidents.filter(
    (i) =>
      (!search ||
        `${i.name} ${i.area || ""}`
          .toLowerCase()
          .includes(search.toLowerCase())) &&
      (!status || i.status === status),
  );
  return (
    <>
      <PageHeading title="Incidents" />
      <MainCard>
        <div className="filters">
          <FilterInput
            label="Search incidents"
            value={search}
            onChange={setSearch}
          />
          <FilterSelect
            label="Incident status"
            value={status}
            onChange={setStatus}
            options={[
              ["", "All statuses"],
              ...[
                ...new Set(incidents.map((i) => i.status).filter(Boolean)),
              ].map((s) => [s, s]),
            ]}
          />
          <ClearFilters
            onClick={() => {
              setSearch("");
              setStatus("");
            }}
          />
        </div>
      </MainCard>
      <MainCard content={false}>
        <div className="table-scroll">
          <Table>
            <TableHead>
              <TableRow>
                {[
                  "Incident",
                  "Status",
                  "Deployed resources",
                  "Structures",
                  "People / groups",
                  "Voice assistant to call",
                  "Human follow-up",
                  "Last assessment",
                ].map((label) => (
                  <TableCell key={label}>{label}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((i) => {
                const totals = metrics([i]),
                  calls = callRows(i);
                return (
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
                    <TableCell>{totals.deployed ?? "—"}</TableCell>
                    <TableCell>{totals.structures}</TableCell>
                    <TableCell>{totals.clusters ?? "—"}</TableCell>
                    <TableCell>
                      {calls.filter((r) => r.toCall).length}
                    </TableCell>
                    <TableCell>
                      {calls.filter((r) => r.followup).length}
                    </TableCell>
                    <TableCell className="nowrap">{stamp(i.as_of)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
        {!filtered.length && <Empty title="No matching incidents" />}
      </MainCard>
    </>
  );
}
