import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Button,
  Typography,
  Alert,
} from "@mui/material";
import {
  Coordinates,
  MainCard,
  Status,
  Empty,
  DetailDialog,
  Facts,
  PageHeading,
  FilterSelect,
  FilterInput,
  ClearFilters,
} from "../components/Common";
import {
  gps,
  buildingRows,
  filterBuildings,
  money,
  count,
  stamp,
  humanize,
} from "../state/model.mjs";
const defaults = {
  incident: "",
  from: "",
  to: "",
  type: "",
  risk: "",
  search: "",
};
export default function Buildings({ incidents, incident }) {
  const [filters, setFilters] = useState(defaults),
    [params, setParams] = useSearchParams();
  const set = (key, value) => setFilters((f) => ({ ...f, [key]: value }));
  const rows = useMemo(
    () => buildingRows(incident ? [incident] : incidents),
    [incident, incidents],
  );
  const filtered = filterBuildings(rows, filters);
  const selected = rows.find(
    (r) =>
      r.asset_id === params.get("building") &&
      (!params.get("incident") || r.incident_id === params.get("incident")),
  );
  const select = (row) =>
    setParams(row ? { building: row.asset_id, incident: row.incident_id } : {});
  return (
    <>
      <PageHeading
        title="Buildings & risk"
        description={
          incident
            ? `Assessments for ${incident.name}`
            : "Valuation and risk assessments across incidents. Filters use assessment date (UTC)."
        }
      />
      <MainCard>
        <div className="filters">
          <FilterInput
            label="Search buildings"
            value={filters.search}
            onChange={(v) => set("search", v)}
            placeholder="Name, location or identifier"
          />
          {!incident && (
            <FilterSelect
              label="Incident"
              value={filters.incident}
              onChange={(v) => set("incident", v)}
              options={[
                ["", "All incidents"],
                ...incidents.map((i) => [i.id, i.name]),
              ]}
            />
          )}
          <FilterInput
            label="Assessment from"
            type="date"
            value={filters.from}
            onChange={(v) => set("from", v)}
          />
          <FilterInput
            label="Assessment to"
            type="date"
            value={filters.to}
            onChange={(v) => set("to", v)}
          />
          <FilterSelect
            label="Building type"
            value={filters.type}
            onChange={(v) => set("type", v)}
            options={[
              ["", "All types"],
              ...[
                ...new Set(rows.map((r) => r.asset_type).filter(Boolean)),
              ].map((v) => [v, humanize(v)]),
            ]}
          />
          <FilterSelect
            label="Risk assessment"
            value={filters.risk}
            onChange={(v) => set("risk", v)}
            options={[
              ["", "All assessments"],
              ...[...new Set(rows.map((r) => r.risk_label))].map((v) => [v, v]),
            ]}
          />
          <ClearFilters onClick={() => setFilters(defaults)} />
        </div>
      </MainCard>
      <MainCard
        title={`Building assessments (${filtered.length})`}
        action={<span className="small-muted">Values in EUR</span>}
        content={false}
      >
        <div className="table-scroll">
          <Table>
            <TableHead>
              <TableRow>
                {[
                  "Building / location",
                  ...(!incident ? ["Incident"] : []),
                  "Risk assessment",
                  "Valuation",
                  "Expected loss",
                  "People estimate",
                  "Remaining window",
                  "Assessed at",
                ].map((c) => (
                  <TableCell key={c}>{c}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((r) => (
                <TableRow key={r.key} hover>
                  <TableCell>
                    <Button
                      className="table-link"
                      onClick={() => select(r)}
                      aria-label={`View building ${r.name}`}
                    >
                      {r.name}
                    </Button>
                    <div className="small-muted">
                      {humanize(r.asset_type)} ·{" "}
                      {r.municipality || "Location not supplied"}
                    </div>
                    <Coordinates location={r} />
                  </TableCell>
                  {!incident && <TableCell>{r.incident_name}</TableCell>}
                  <TableCell>
                    <span className="risk-status">
                      <Status value={r.risk_label} />
                    </span>
                    <div className="small-muted">
                      {r.risk_score !== null
                        ? `Score ${r.risk_score} · supplied`
                        : "Score not supplied"}
                    </div>
                  </TableCell>
                  <TableCell className="nowrap">
                    {money(r.replacement_value_eur)}
                  </TableCell>
                  <TableCell className="nowrap">
                    {money(r.expected_loss_eur_mid)}
                    <div className="small-muted">
                      {money(r.expected_loss_eur_low)} –{" "}
                      {money(r.expected_loss_eur_high)}
                    </div>
                  </TableCell>
                  <TableCell>{count(r.estimated_occupancy)}</TableCell>
                  <TableCell>{count(r.contact?.slack_min)} min</TableCell>
                  <TableCell className="nowrap">
                    {stamp(r.assessed_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        {!filtered.length && (
          <Empty title="No matching buildings">
            Adjust the filters to include more assessments.
          </Empty>
        )}
      </MainCard>
      <Typography variant="body2" color="text.secondary">
        Valuations and forecast probabilities may be estimates. Contact priority
        remains the backend’s remaining evacuation window. Only supplied
        snapshots are shown; historical assessments are not available from the
        current-state API.
      </Typography>
      <DetailDialog
        title={selected?.name || "Building details"}
        open={!!selected}
        onClose={() => select(null)}
      >
        {selected && (
          <>
            <Alert severity="info" sx={{ mb: 2 }}>
              Assessment evidence, not an evacuation or dispatch instruction.
            </Alert>
            <Facts
              rows={[
                ["Incident", selected.incident_name],
                ["GPS (latitude, longitude)", gps(selected)],
                ["Assessment time", stamp(selected.assessed_at)],
                ["Type", humanize(selected.asset_type)],
                ["Estimated occupancy", count(selected.estimated_occupancy)],
                ["Occupancy basis", selected.occupancy_basis],
                ["Replacement value", money(selected.replacement_value_eur)],
                ["Valuation basis", selected.replacement_value_basis],
                ["Expected loss", money(selected.expected_loss_eur_mid)],
                [
                  "Loss range",
                  `${money(selected.expected_loss_eur_low)} – ${money(selected.expected_loss_eur_high)}`,
                ],
                ["Risk score", count(selected.risk_score)],
                [
                  "Burn probability",
                  selected.burn_probability == null
                    ? "Not supplied"
                    : `${Math.round(selected.burn_probability * 100)}%`,
                ],
                ["Criticality", humanize(selected.criticality_tier)],
                ["Operational value score", count(selected.value_score)],
                [
                  "Remaining window",
                  `${count(selected.contact?.slack_min)} min`,
                ],
              ]}
            />
            <Typography variant="h5" sx={{ mt: 3, mb: 1 }}>
              Sources & evidence
            </Typography>
            {selected.sources?.length ? (
              selected.sources.map((s, n) => (
                <div className="evidence" key={n}>
                  <strong>{s.source || "Source not supplied"}</strong>
                  <p>{(s.fields || []).join(", ")}</p>
                  <p>{s.notes}</p>
                  <small>{stamp(s.observed_at)}</small>
                </div>
              ))
            ) : (
              <Empty title="No recorded sources" />
            )}
          </>
        )}
      </DetailDialog>
    </>
  );
}
