import { useEffect, useState, useMemo } from "react";
import { DashboardClient } from "./transport.mjs";
import { toIncident } from "./model.mjs";
import { demoIncidents } from "./demo.mjs";
import { onboardingIncidents } from "../onboarding/generate.mjs";
// source: connected (live API) | design_demo (shipped scenario) | onboarding (this session's form).
export function useDashboard(source, onboarding) {
  const [live, setLive] = useState(null),
    [status, setStatus] = useState({
      connection: "connecting",
      stale: false,
      errors: 0,
    });
  useEffect(() => {
    if (source !== "connected") return;
    const events = new Map();
    let sequence = 0;
    const client = new DashboardClient({
      fetchState: async () => {
        const response = await fetch("/api/state", {
          cache: "no-store",
          signal: AbortSignal.timeout(10000),
        });
        if (!response.ok) throw new Error("State unavailable");
        return response.json();
      },
      WebSocket: globalThis.WebSocket,
      socketUrl: `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/updates`,
      onState: (state) => {
        const id = state.incident_id || state.scenario_id;
        for (const [index, event] of (state.events || []).entries()) {
          const key = `${id}:${event.event_id || `${state.revision}:${index}`}`;
          if (!events.has(key))
            events.set(key, {
              ...event,
              event_id: key,
              received_at: new Date().toISOString(),
              ingestion_sequence: ++sequence,
              received_basis: "Browser session receipt",
              incident_id: id,
            });
        }
        setLive(
          toIncident({
            ...state,
            events: [...events.values()].filter((e) => e.incident_id === id),
          }),
        );
      },
      onStatus: setStatus,
    });
    client.start();
    return () => client.stop();
  }, [source]);
  const incidents = useMemo(() => {
    if (source === "onboarding")
      return onboarding ? onboardingIncidents(onboarding) : [];
    if (source === "design_demo") return demoIncidents;
    return live ? [live] : [];
  }, [source, onboarding, live]);
  return {
    incidents,
    status:
      source === "connected"
        ? status
        : {
            connection: source === "onboarding" ? "session" : "demo",
            stale: false,
            errors: 0,
          },
  };
}
