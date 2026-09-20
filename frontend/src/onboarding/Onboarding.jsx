import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Chip,
  LinearProgress,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import CheckOutlined from "@ant-design/icons/CheckOutlined";
import { MainCard, PageHeading } from "../components/Common";
import { humanize } from "../state/model.mjs";
import {
  CATALONIA,
  RESOURCE_TYPES,
  PLACEMENTS,
  buildConfig,
  defaultDraft,
  draftFromConfig,
  nearestPlace,
  problemsFor,
  summarise,
} from "./config.mjs";
import "./onboarding.css";

// A fixed instant keeps the preview stable while the form is edited; the saved
// configuration is stamped with the real time when it is applied.
const PREVIEW_AT = "2026-01-01T12:00:00.000Z";

const STEPS = [
  {
    id: "jurisdiction",
    title: "Jurisdiction",
    blurb: "Who is responding, and where they start from.",
  },
  {
    id: "fires",
    title: "Fires",
    blurb: "One GPS pair per fire, inside Catalonia.",
  },
  {
    id: "calls",
    title: "Call list",
    blurb: "The numbers the voice agent works through.",
  },
  {
    id: "resources",
    title: "Resources",
    blurb: "How many of each type, and where they start.",
  },
  {
    id: "start",
    title: "Start demo",
    blurb: "Run a simulated response on what you entered.",
  },
];

const FINAL = STEPS.at(-1).id;

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

function Blocking({ messages }) {
  if (!messages.length) return null;
  return (
    <Alert severity="error" className="onboarding-blocking" role="alert">
      <strong>
        {messages.length === 1
          ? "This step needs one more thing"
          : "This step needs a few more things"}
      </strong>
      <ul>
        {messages.map((message) => (
          <li key={message}>{message}</li>
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

function Progress({ step, states, onSelect }) {
  const percent = ((step + 1) / STEPS.length) * 100;
  return (
    <div className="onboarding-progress">
      <LinearProgress
        variant="determinate"
        value={percent}
        aria-label={`Step ${step + 1} of ${STEPS.length}: ${STEPS[step].title}`}
      />
      <ol className="onboarding-steps">
        {STEPS.map((entry, index) => (
          <li key={entry.id}>
            <button
              type="button"
              className={`onboarding-step ${states[entry.id]}`}
              aria-current={index === step ? "step" : undefined}
              onClick={() => onSelect(index)}
            >
              <span className="onboarding-step-mark" aria-hidden="true">
                {states[entry.id] === "complete" && index !== step ? (
                  <CheckOutlined />
                ) : (
                  index + 1
                )}
              </span>
              <span>
                <strong>{entry.title}</strong>
                <small>{entry.blurb}</small>
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function Onboarding({ config, onSave, onClear }) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState(() =>
    config ? draftFromConfig(config) : defaultDraft(),
  );
  const [step, setStep] = useState(0);
  const [attempted, setAttempted] = useState(() => new Set());
  const [failure, setFailure] = useState(null);
  const set = (key, value) => {
    setDraft((previous) => ({ ...previous, [key]: value }));
    setFailure(null);
  };
  const setResource = (type, key, value) =>
    setDraft((previous) => ({
      ...previous,
      resources: {
        ...previous.resources,
        [type]: { ...previous.resources[type], [key]: value },
      },
    }));

  const built = buildConfig(draft, { createdAt: PREVIEW_AT });
  const ready = !built.problems.length;
  const totals = summarise(built.config);
  const current = STEPS[step];
  const blocking = problemsFor(built.problems, current.id);
  const states = Object.fromEntries(
    STEPS.map((entry) => {
      // The final step cannot start the demo until every other step is satisfied.
      const outstanding =
        entry.id === FINAL
          ? built.problems.length
          : problemsFor(built.problems, entry.id).length;
      return [
        entry.id,
        outstanding
          ? attempted.has(entry.id)
            ? "invalid"
            : "todo"
          : "complete",
      ];
    }),
  );
  const advance = (next) => {
    if (next > step && blocking.length) {
      setAttempted((previous) => new Set(previous).add(current.id));
      return;
    }
    setStep(Math.max(0, Math.min(STEPS.length - 1, next)));
    setFailure(null);
  };
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
      setAttempted(new Set());
      setStep(0);
      setFailure(null);
    } catch (error) {
      setFailure(error.message);
    }
  };

  return (
    <div className="onboarding">
      <PageHeading
        title="Demo onboarding"
        description="Describe a jurisdiction, then run a simulated response on it. Nothing here is dispatched, called or saved to the backend."
      />
      <Progress step={step} states={states} onSelect={advance} />

      <MainCard
        title={`${step + 1}. ${current.title}`}
        action={
          <Chip
            size="small"
            variant="outlined"
            label={`Step ${step + 1} of ${STEPS.length}`}
          />
        }
      >
        {attempted.has(current.id) && <Blocking messages={blocking} />}

        {current.id === "jurisdiction" && (
          <div className="onboarding-grid">
            <Field
              label="Fire department name"
              hint="Shown as the source of every resource record."
            >
              <input
                value={draft.department}
                onChange={(event) => set("department", event.target.value)}
                placeholder="Bombers de la Bisbal"
              />
            </Field>
            <Field
              label="Fire station GPS (latitude, longitude)"
              hint="Where resources placed “at the fire station” start. Needed only if a resource type uses that placement."
            >
              <input
                value={draft.station}
                onChange={(event) => set("station", event.target.value)}
                placeholder="41.9600, 3.0380"
              />
            </Field>
          </div>
        )}

        {current.id === "fires" && (
          <>
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
          </>
        )}

        {current.id === "calls" && (
          <>
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
              {totals.phones} number{totals.phones === 1 ? "" : "s"} accepted.
              They are spread evenly across the fires; a fire with fewer numbers
              than locations leaves the rest flagged for human follow-up.
            </Typography>
          </>
        )}

        {current.id === "resources" && (
          <>
            <div className="table-scroll">
              <Table>
                <TableHead>
                  <TableRow>
                    {["Type", "Capabilities", "Count", "Starting location"].map(
                      (column) => (
                        <TableCell key={column}>{column}</TableCell>
                      ),
                    )}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {RESOURCE_TYPES.map((type) => (
                    <TableRow key={type.id}>
                      <TableCell>
                        <strong>{type.label}</strong>
                      </TableCell>
                      <TableCell className="small-muted">
                        {type.capabilities.map(humanize).join(" · ")}
                      </TableCell>
                      <TableCell>
                        <input
                          className="onboarding-count"
                          type="number"
                          min="0"
                          max="40"
                          aria-label={`${type.label} count`}
                          value={draft.resources[type.id]?.count ?? 0}
                          onChange={(event) =>
                            setResource(type.id, "count", event.target.value)
                          }
                        />
                      </TableCell>
                      <TableCell>
                        <select
                          aria-label={`${type.label} starting location`}
                          value={
                            draft.resources[type.id]?.placement ?? "station"
                          }
                          onChange={(event) =>
                            setResource(
                              type.id,
                              "placement",
                              event.target.value,
                            )
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
            <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
              {totals.resources} resource{totals.resources === 1 ? "" : "s"} in
              total, shared across the supplied fires in turn. “Random across
              the territory” draws a point from the area covering the fires and
              the station, widened by about 13 km and clipped to{" "}
              {CATALONIA.label}.
            </Typography>
          </>
        )}

        {current.id === "start" &&
          (ready ? (
            <>
              <Typography>
                {totals.fires} fire{totals.fires === 1 ? "" : "s"},{" "}
                {totals.phones} number{totals.phones === 1 ? "" : "s"} to call
                and {totals.resources} resource
                {totals.resources === 1 ? "" : "s"} for{" "}
                {built.config.department}.
              </Typography>
              <Alert severity="info" sx={{ mt: 2 }}>
                Starting the demo opens the dashboard on this jurisdiction so
                you can work it as if it were live. Every location, occupancy
                figure, value, risk score, call and response plan is simulated;
                no call is placed and no resource is dispatched. It is held for
                this browser session only — end it and the dashboard returns to
                its configured data source.
              </Alert>
            </>
          ) : (
            <Alert severity="warning">
              <strong>Finish these steps before starting the demo</strong>
              <ul>
                {STEPS.filter(
                  (entry) => problemsFor(built.problems, entry.id).length,
                ).map((entry) => (
                  <li key={entry.id}>
                    <button
                      type="button"
                      className="onboarding-jump"
                      onClick={() =>
                        setStep(STEPS.findIndex((s) => s.id === entry.id))
                      }
                    >
                      {entry.title}
                    </button>
                    : {problemsFor(built.problems, entry.id).join(" ")}
                  </li>
                ))}
              </ul>
            </Alert>
          ))}
      </MainCard>

      {failure && <Alert severity="error">{failure}</Alert>}
      <div className="onboarding-actions">
        <Button
          variant="outlined"
          disabled={step === 0}
          onClick={() => advance(step - 1)}
        >
          Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button variant="contained" onClick={() => advance(step + 1)}>
            Next
          </Button>
        ) : (
          <Button variant="contained" disabled={!ready} onClick={apply}>
            {config ? "Restart demo" : "Start demo"}
          </Button>
        )}
        <span className="onboarding-actions-spacer" />
        {config && <Button onClick={reset}>End demo</Button>}
        <Button onClick={() => navigate("/overview")}>Back to dashboard</Button>
      </div>
    </div>
  );
}
