import { useMemo, useRef, useState } from "react";
import { Alert } from "@mui/material";
import { useApprovals } from "../state/approvals";
import CrewPlanMap from "./CrewPlanMap";
import { CrewStopTable } from "./CrewPlans";

export default function CrewItinerary({ incident, team, name }) {
  const approval = useApprovals().info(incident, team.team_id);
  const reviewed = approval.plan?.reviewed_plan;
  const shownTeam = useMemo(
    () => (reviewed ? { ...team, ...reviewed } : team),
    [team, reviewed],
  );
  const [activeStopId, setActiveStopId] = useState("__start__");
  const table = useRef(null);
  const selectFromMap = (id) => {
    setActiveStopId(id);
    if (!id) return;
    const container = table.current?.querySelector(".crew-stop-table-wrap");
    const row = [...(container?.querySelectorAll("[data-stop-id]") || [])].find(
      (item) => item.dataset.stopId === id,
    );
    if (!container || !row) return;
    const top =
      row.getBoundingClientRect().top - container.getBoundingClientRect().top;
    const header =
      container.querySelector("thead")?.getBoundingClientRect().height || 0;
    if (top < header || top + row.offsetHeight > container.clientHeight)
      container.scrollTop += top - header;
  };
  return (
    <div className="crew-itinerary">
      {reviewed && (
        <Alert severity="success">
          Analyst-approved visit order · {approval.plan.approval.analyst}.
          Dispatch remains separate.
        </Alert>
      )}
      <CrewPlanMap
        team={shownTeam}
        assets={incident.assets}
        name={name}
        activeStopId={activeStopId}
        onActiveStopChange={selectFromMap}
      />
      <div ref={table}>
        <CrewStopTable
          incident={incident}
          team={shownTeam}
          activeStopId={activeStopId}
          onActiveStopChange={setActiveStopId}
          editable={false}
          draft={false}
          showOrderControls={false}
        />
      </div>
    </div>
  );
}
