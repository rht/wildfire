import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { MainCard, PageHeading } from "../components/Common";
import IncidentMap from "../components/IncidentMap";
import { count } from "../state/model.mjs";
import {
  CATALONIA,
  CREW_TYPES,
  PLACEMENTS,
  buildConfig,
  defaultDraft,
  draftFromConfig,
  nearestPlace,
  summarise,
} from "./config.mjs";
import { onboardingIncidents } from "./generate.mjs";
import "./onboarding.css";

// A fixed instant keeps the preview stable while the form is edited; the saved
// configuration is stamped with the real time when it is applied.
const PREVIEW_AT = "2026-01-01T12:00:00.000Z";

function Problems({ title, problems }) {
  if (!problems.length) return null;
  return (
    <Alert severity="warning" className="onboarding-problems">
      <strong>{title}</strong>
      <ul>
        {problems.map((problem) => (
          <li key={`${problem.line}:${problem.raw}`}>
            Line {problem.line}: {problem.reason} — <code>{problem.raw}</code>
          </li>
        ))}
      </ul>
    </Alert>
  );
}

function Field({ label, hint, children }) {
  return (
    <label className="onboarding-field">
      <span className="onboarding-label">{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export default function Onboarding({ config, onSave, onClear }) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState(() =>
    config ? draftFromConfig(config) : defaultDraft(),
  );
  const [failure, setFailure] = useState(null);
  const set = (key, value) => {
    setDraft((previous) => ({ ...previous, [key]: value }));
    setFailure(null);
  };
  const setCrew = (type, key, value) =>
    setDraft((previous) => ({
      ...previous,
      crews: {
        ...previous.crews,
        [type]: { ...previous.crews[type], [key]: value },
      },
    }));

  const built = buildConfig(draft, { createdAt: PREVIEW_AT });
  const ready = !built.problems.length;
  const totals = summarise(built.config);
  const preview = useMemo(
    () => (ready ? onboardingIncidents(built.config) : []),
    // The generated preview depends only on the canonical configuration.
    [ready, JSON.stringify(built.config)],
  );

  const apply = () => {
    try {
      onSave({ ...built.config, created_at: new Date().toISOString() });
      navigate("/overview");
    } catch (error) {
      setFailure(error.message);
    }
  };
  const reset = () => {
    try {
      onClear();
      setDraft(defaultDraft());
      setFailure(null);
    } catch (error) {
      setFailure(error.message);
    }
  };

  return (
    <div className="onboarding">
      <PageHeading
        title="Demo onboarding"
        description="Describe a jurisdiction and this builds a dashboard from it. Nothing here is dispatched, called or saved to the backend."
      />
      <Alert severity="info" className="mode-banner">
        Every generated location, occupancy figure, value, risk score, call and
        crew plan is synthetic. The configuration is held for this browser
        session only; clear it and the dashboard returns to its configured data
        source.
      </Alert>

      <MainCard title="Jurisdiction">
        <div className="onboarding-grid">
          <Field
            label="Fire department name"
            hint="Shown as the source of every crew record."
          >
            <input
              value={draft.department}
              onChange={(event) => set("department", event.target.value)}
              placeholder="Bombers de la Bisbal"
            />
          </Field>
          <Field
            label="Fire station GPS (latitude, longitude)"
            hint="Where crews placed “at the fire station” start. Required only if a crew type uses that placement."
          >
            <input
              value={draft.station}
              onChange={(event) => set("station", event.target.value)}
              placeholder="41.9600, 3.0380"
            />
          </Field>
        </div>
        {draft.station.trim() && !built.station && (
          <Alert severity="warning">
            The station pair is unreadable or outside {CATALONIA.label}.
          </Alert>
        )}
      </MainCard>

      <MainCard title={`Fires (${totals.fires})`}>
        <Field
          label="GPS pairs, one per line"
          hint={`One pair makes one fire. Pairs must fall inside ${CATALONIA.label}; latitude first.`}
        >
          <textarea
            rows={Math.max(4, built.fires.points.length + 2)}
            value={draft.fires}
            onChange={(event) => set("fires", event.target.value)}
            placeholder={"41.9400, 3.0400\n41.8600, 2.9100"}
          />
        </Field>
        <Problems
          title="Ignored coordinate lines"
          problems={built.fires.problems}
        />
        {built.fires.points.length > 0 && (
          <div className="table-scroll">
            <Table size="small">
              <TableHead>
                <TableRow>
                  {[
                    "Fire",
                    "Latitude",
                    "Longitude",
                    "Nearest reference place",
                  ].map((column) => (
                    <TableCell key={column}>{column}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {built.fires.points.map((point, index) => (
                  <TableRow
                    key={`${point.latitude}:${point.longitude}:${index}`}
                  >
                    <TableCell>{index + 1}</TableCell>
                    <TableCell>{point.latitude}</TableCell>
                    <TableCell>{point.longitude}</TableCell>
                    <TableCell>
                      {nearestPlace(point).name}
                      <div className="small-muted">
                        Label only, from a short built-in list of towns.
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </MainCard>

      <MainCard title={`Voice agent call list (${totals.phones})`}>
        <Field
          label="Phone numbers, one per line"
          hint="Each number becomes one contactable location in the call queue. No call is placed from this dashboard."
        >
          <textarea
            rows={Math.max(4, built.phones.numbers.length + 2)}
            value={draft.phones}
            onChange={(event) => set("phones", event.target.value)}
            placeholder={"+34600111222\n+34600333444"}
          />
        </Field>
        <Problems
          title="Ignored number lines"
          problems={built.phones.problems}
        />
        <Typography variant="body2" color="text.secondary">
          Numbers are spread evenly across the fires. A fire with fewer numbers
          than locations leaves the rest flagged for human follow-up.
        </Typography>
      </MainCard>

      <MainCard
        title={`Crews in the jurisdiction (${totals.crews})`}
        content={false}
      >
        <div className="table-scroll">
          <Table>
            <TableHead>
              <TableRow>
                {["Type", "Capabilities", "Crews", "Starting location"].map(
                  (column) => (
                    <TableCell key={column}>{column}</TableCell>
                  ),
                )}
              </TableRow>
            </TableHead>
            <TableBody>
              {CREW_TYPES.map((type) => (
                <TableRow key={type.id}>
                  <TableCell>
                    <strong>{type.label}</strong>
                  </TableCell>
                  <TableCell className="small-muted">
                    {type.capabilities.join(" · ")}
                  </TableCell>
                  <TableCell>
                    <input
                      className="onboarding-count"
                      type="number"
                      min="0"
                      max="40"
                      aria-label={`${type.label} count`}
                      value={draft.crews[type.id]?.count ?? 0}
                      onChange={(event) =>
                        setCrew(type.id, "count", event.target.value)
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <select
                      aria-label={`${type.label} starting location`}
                      value={draft.crews[type.id]?.placement ?? "station"}
                      onChange={(event) =>
                        setCrew(type.id, "placement", event.target.value)
                      }
                    >
                      {PLACEMENTS.map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <Typography variant="body2" color="text.secondary" sx={{ p: 2.5 }}>
          Crews are shared across the supplied fires in turn. “Random across the
          territory” draws a point from the area covering the fires and the
          station, widened by about 13 km and clipped to {CATALONIA.label}.
        </Typography>
      </MainCard>

      <MainCard
        title="Preview"
        action={
          <Chip
            size="small"
            color={ready ? "success" : "warning"}
            label={ready ? "Ready to build" : "Incomplete"}
          />
        }
      >
        {ready ? (
          <>
            <div className="onboarding-summary">
              <div>
                <strong>{count(totals.fires)}</strong>
                <span>{totals.fires === 1 ? "fire" : "fires"}</span>
              </div>
              <div>
                <strong>{count(totals.phones)}</strong>
                <span>numbers to call</span>
              </div>
              <div>
                <strong>{count(totals.crews)}</strong>
                <span>crews</span>
              </div>
              <div>
                <strong>
                  {count(
                    preview.reduce(
                      (total, incident) => total + incident.assets.length,
                      0,
                    ),
                  )}
                </strong>
                <span>generated locations</span>
              </div>
            </div>
            <IncidentMap incidents={preview} interactive={false} />
          </>
        ) : (
          <Alert severity="warning">
            <strong>Finish the configuration to build a dashboard</strong>
            <ul>
              {built.problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </Alert>
        )}
      </MainCard>

      {failure && <Alert severity="error">{failure}</Alert>}
      <div className="onboarding-actions">
        <Button variant="contained" disabled={!ready} onClick={apply}>
          {config ? "Rebuild dashboard" : "Build dashboard"}
        </Button>
        {config && (
          <Button variant="outlined" onClick={reset}>
            Clear session data
          </Button>
        )}
        <Button onClick={() => navigate("/overview")}>Back to dashboard</Button>
      </div>
    </div>
  );
}
