import { Button } from "@mui/material";
import { stamp } from "../state/model.mjs";
export default function TimelineControls({ timeline }) {
  const { entries, index, historical, simulated, select, latest } = timeline;
  const selected = entries[index];
  const momentLabel = selected
    ? `${simulated ? `Stage ${index + 1} of ${entries.length} · ` : ""}${stamp(selected.at)} · revision ${selected.revision}`
    : "Waiting for a snapshot";
  return (
    <section
      className={`timeline-controls${historical ? " reviewing" : ""}`}
      aria-label="Time travel"
    >
      <div className="timeline-heading">
        <strong>Time travel</strong>
        <span>
          {simulated
            ? historical
              ? "Simulated spread · View only"
              : "Current demo perimeter"
            : historical
              ? "Earlier snapshot · View only"
              : "Current state"}
        </span>
      </div>
      <div className="timeline-track">
        <Button
          size="small"
          disabled={!index}
          onClick={() => select(index - 1)}
        >
          Previous
        </Button>
        <input
          aria-label="Snapshot time"
          type="range"
          min="0"
          max={Math.max(0, entries.length - 1)}
          value={index}
          disabled={entries.length < 2}
          onChange={(e) => select(Number(e.target.value))}
          aria-valuetext={selected ? momentLabel : "No snapshots received"}
        />
        <Button
          size="small"
          disabled={index >= entries.length - 1}
          onClick={() => select(index + 1)}
        >
          Next
        </Button>
        {historical && (
          <Button size="small" variant="outlined" onClick={latest}>
            Return to current
          </Button>
        )}
      </div>
      <div className="timeline-caption">
        {momentLabel}
        <span>
          {entries.length} {entries.length === 1 ? "moment" : "moments"} ·{" "}
          {timeline.historyLabel}
        </span>
      </div>
      {simulated && (
        <div className="timeline-notice">
          Illustrative simulated spread, not a predictive fire model or live
          observation. Scrub to compare scaled demo perimeters. Risk values and
          distances are not recalculated.
        </div>
      )}
      {historical && (
        <div className="timeline-notice">
          Reviewing an earlier snapshot. Return to current state to confirm a
          crew plan.
        </div>
      )}
      {timeline.storageError && (
        <div className="timeline-notice">
          Saved history is unavailable. Only snapshots received now are shown.
        </div>
      )}
    </section>
  );
}
