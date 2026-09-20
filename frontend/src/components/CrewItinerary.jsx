import { useMemo, useRef, useState } from "react";
import { useApprovals } from "../state/approvals";
import { crewMapStopId, crewMapsData, crewStopId } from "../state/crew-map.mjs";
import { MainCard } from "./Common";
import CrewPlanMap from "./CrewPlanMap";
import { CrewStopTable } from "./CrewPlans";

export default function CrewItinerary({ incident, teams }) {
  const approvals = useApprovals();
  const entries = useMemo(
    () =>
      teams.map((team) => {
        const approval = approvals.info(incident, team.team_id);
        return {
          approval,
          team: {
            ...team,
            ...approval.plan?.reviewed_plan,
            name:
              incident.teams.find((item) => item.team_id === team.team_id)
                ?.name || team.team_id,
          },
        };
      }),
    [teams, incident, approvals],
  );
  const shownTeams = useMemo(
    () => entries.map((entry) => entry.team),
    [entries],
  );
  const mapData = useMemo(
    () => crewMapsData(shownTeams, incident.assets),
    [shownTeams, incident.assets],
  );
  const [activeStopId, setActiveStopId] = useState(null);
  const tables = useRef(null);
  const selectFromMap = (id) => {
    setActiveStopId(id);
    if (!id || !tables.current) return;
    for (const section of tables.current.querySelectorAll("[data-crew-id]")) {
      const row = [...section.querySelectorAll("tr[data-stop-id]")].find(
        (item) =>
          crewMapStopId(section.dataset.crewId, item.dataset.stopId) === id,
      );
      if (!row) continue;
      const bounds = tables.current.getBoundingClientRect();
      const target = row.getBoundingClientRect();
      const header =
        (section.querySelector(".MuiCardHeader-root")?.offsetHeight || 0) +
        (section.querySelector("thead")?.offsetHeight || 0) +
        2;
      if (target.top < bounds.top + header || target.bottom > bounds.bottom)
        tables.current.scrollTop += target.top - bounds.top - header;
      tables.current.scrollLeft = 0;
      break;
    }
  };
  return (
    <div className="incident-crew-plan crew-review-layout">
      <CrewPlanMap
        teams={shownTeams}
        assets={incident.assets}
        name={incident.name}
        activeStopId={activeStopId}
        onActiveStopChange={selectFromMap}
      />
      <div className="plan-teams" ref={tables}>
        {entries.map(({ team, approval }, index) => {
          const localId = [
            "__start__",
            ...(team.tasks || []).map(crewStopId),
          ].find((id) => crewMapStopId(team.team_id, id) === activeStopId);
          return (
            <section
              className="crew-table-section"
              key={team.team_id}
              data-crew-id={team.team_id}
              style={{ borderTopColor: mapData.crews[index].color }}
            >
              <MainCard title={team.name} content={false}>
                {approval.plan?.reviewed_plan && (
                  <div className="crew-overview-approval">
                    Analyst-approved visit order ·{" "}
                    {approval.plan.approval.analyst}
                  </div>
                )}
                <CrewStopTable
                  incident={incident}
                  team={team}
                  activeStopId={localId}
                  editable={false}
                  draft={false}
                  showOrderControls={false}
                  onActiveStopChange={(id) =>
                    setActiveStopId(id ? crewMapStopId(team.team_id, id) : null)
                  }
                />
              </MainCard>
            </section>
          );
        })}
      </div>
    </div>
  );
}
