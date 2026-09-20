import { useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { Link } from "react-router-dom";
import {
  MainCard,
  PageHeading,
  FilterSelect,
  FilterInput,
  ClearFilters,
  Status,
  Empty,
} from "../components/Common";
import { orderedEvents, stamp, humanize } from "../state/model.mjs";
const defaults = {
  incident: "",
  from: "",
  to: "",
  kind: "",
  severity: "",
  order: "newest",
};
export default function ActivityLog({ incidents, incident, demo }) {
  const [filters, setFilters] = useState(defaults);
  const set = (key, value) => setFilters((f) => ({ ...f, [key]: value }));
  const scope = incident ? [incident] : incidents,
    all = scope.flatMap((i) => i.events),
    events = orderedEvents(scope, filters);
  return (
    <>
      <PageHeading
        title="Activity log"
        description="Incoming events, in the order they were received. Open related records for context."
      />
      <MainCard>
        <div className="filters">
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
            label="Received from"
            type="date"
            value={filters.from}
            onChange={(v) => set("from", v)}
          />
          <FilterInput
            label="Received to"
            type="date"
            value={filters.to}
            onChange={(v) => set("to", v)}
          />
          <FilterSelect
            label="Event type"
            value={filters.kind}
            onChange={(v) => set("kind", v)}
            options={[
              ["", "All event types"],
              ...[...new Set(all.map((e) => e.kind).filter(Boolean))].map(
                (v) => [v, humanize(v)],
              ),
            ]}
          />
          <FilterSelect
            label="Severity"
            value={filters.severity}
            onChange={(v) => set("severity", v)}
            options={[
              ["", "All severities"],
              ["info", "Information"],
              ["warning", "Warning"],
              ["error", "Error"],
            ]}
          />
          <FilterSelect
            label="Event order"
            value={filters.order}
            onChange={(v) => set("order", v)}
            options={[
              ["newest", "Newest received first"],
              ["oldest", "Oldest received first"],
            ]}
          />
          <ClearFilters onClick={() => setFilters(defaults)} />
        </div>
      </MainCard>
      <MainCard
        title={`Event stream (${events.length})`}
        action={
          <span className="small-muted">
            {demo
              ? "Illustrative ingestion sequence"
              : "Browser-session receipt order"}
          </span>
        }
        content={false}
      >
        <div className="table-scroll">
          <Table>
            <TableHead>
              <TableRow>
                {[
                  "Order",
                  "Received at",
                  "Event time",
                  "Incident",
                  "Type",
                  "Description / related record",
                  "Severity",
                  "Source",
                ].map((c) => (
                  <TableCell key={c}>{c}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {events.map((e) => (
                <TableRow key={e.key}>
                  <TableCell>
                    <span className="sequence">
                      {e.ingestion_sequence ?? "—"}
                    </span>
                  </TableCell>
                  <TableCell className="nowrap">
                    {stamp(e.received_at)}
                  </TableCell>
                  <TableCell className="nowrap">
                    {stamp(e.as_of || e.observed_at)}
                  </TableCell>
                  <TableCell>
                    <Link
                      to={`/incidents/${encodeURIComponent(e.incident_id)}/summary`}
                    >
                      {e.incident_name}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Status value={e.kind} />
                  </TableCell>
                  <TableCell>
                    {e.notes || e.reason || "No description supplied"}
                    {e.asset_id && (
                      <div>
                        <Link
                          to={`/incidents/${encodeURIComponent(e.incident_id)}/buildings?building=${encodeURIComponent(e.asset_id)}`}
                        >
                          Open location
                        </Link>
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <Status value={e.severity} />
                  </TableCell>
                  <TableCell>{e.source || "Coordination state"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        {!events.length && <Empty title="No matching events" />}
      </MainCard>
      <Typography color="text.secondary" variant="body2">
        {demo
          ? "This event history is an illustrative fixture. No operational actions were performed."
          : "This feed retains supplied events received during this browser session. Received time is browser receipt time, not server ingestion time. Earlier history is not provided by the current-state API; reloading starts a new session."}
      </Typography>
    </>
  );
}
