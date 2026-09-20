import React from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import * as Crew from "../../src/components/CrewPlans.jsx";
import "../../src/styles.css";
const incident = {
  id: "synthetic-review",
  assets: [{ asset_id: "care", name: "Care home", estimated_occupancy: 16 }],
  teams: [
    { team_id: "a", name: "Engine 12" },
    { team_id: "b", name: "Rescue 4" },
  ],
  calls: [],
  plan: {
    locations: [],
    response: {
      review: [
        {
          action_id: "urgent",
          asset_id: "care",
          reason: "urgent_intervention_review",
          human_decision_required: true,
          reasons: ["deadline_exceeded"],
          candidate_attempts: [
            { team_id: "a", finish_min: 40, deadline_min: 30 },
          ],
        },
      ],
    },
  },
};
const team = {
  team_id: "a",
  tasks: [
    {
      action_id: "rescue",
      asset_id: "care",
      status: "proposed",
      action: "evacuate",
      mission_status: "complete_evacuation",
      delivered_people: 16,
      mission_delivered_people: 24,
      trip_count: 2,
      team_ids: ["a", "b"],
      required_team_count: 2,
      evacuation: {
        destination_id: "Reception North",
        unload_min: 3,
        places_reserved: 16,
      },
      mission_legs: [
        {
          kind: "delivery",
          from_node: "care",
          to_node: "reception",
          depart_min: 5,
          arrive_min: 10,
          finish_min: 13,
          people: 8,
          route_source: "confirmed road",
        },
        {
          kind: "return",
          from_node: "reception",
          to_node: "care",
          depart_min: 13,
          arrive_min: 18,
          finish_min: 18,
        },
        {
          kind: "delivery",
          from_node: "care",
          to_node: "reception",
          depart_min: 20,
          arrive_min: 25,
          finish_min: 28,
          people: 8,
        },
      ],
      sensitivity: {
        status: "fragile",
        stress_finish_min: 38,
        stress_deadline_min: 30,
        reasons: ["stress_deadline_exceeded"],
      },
      unknown_dimensions: ["replacement_value_eur"],
      ordering_evidence: { people_gain: 16 },
    },
    {
      action_id: "pickup",
      asset_id: "care",
      status: "proposed",
      mission_status: "pickup_only",
    },
  ],
};
const Urgent = Crew.UrgentInterventionReviews;
createRoot(document.getElementById("root")).render(
  <ThemeProvider theme={createTheme()}>
    <main style={{ padding: 20 }}>
      {Urgent ? (
        <Urgent incident={incident} />
      ) : (
        <p>Urgent review unavailable</p>
      )}
      {Urgent && (
        <section aria-label="Valuation review">
          <Urgent
            incident={{
              ...incident,
              plan: {
                response: {
                  review: [
                    {
                      asset_id: "care",
                      reason: "intervention_review",
                      human_decision_required: true,
                      reasons: ["unknown_value"],
                    },
                  ],
                },
              },
            }}
          />
        </section>
      )}
      <Crew.CrewStopTable
        incident={incident}
        team={team}
        editable
        onMove={() => {
          throw Error("Coordinated work must not reorder");
        }}
        onActiveStopChange={() => {}}
      />
    </main>
  </ThemeProvider>,
);
