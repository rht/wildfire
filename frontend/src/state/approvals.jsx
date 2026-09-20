import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

const ApprovalContext = createContext(null);
const contextKey = (incident) =>
  JSON.stringify([incident.id, incident.snapshot_id, incident.revision]);
const requestFields = (source, incident) => ({
  source,
  incident_id: incident.id,
  snapshot_id: incident.snapshot_id,
  revision: incident.revision,
});
const explanation = (code) =>
  ({
    plan_changed:
      "The plan changed. Reload the dashboard to review the latest plan before confirming.",
    plan_not_confirmable: "This crew has no proposed plan to confirm.",
    origin_not_allowed: "Confirmation must be sent from this dashboard.",
    approval_unavailable:
      "Confirmations are temporarily unavailable. Try again.",
  })[code] || "Could not save or load confirmations. Try again.";

async function responseData(response, expected) {
  const data = await response.json();
  if (!response.ok) throw new Error(explanation(data.error));
  if (
    data.source !== expected.source ||
    data.incident_id !== expected.incident_id ||
    data.snapshot_id !== expected.snapshot_id ||
    data.revision !== expected.revision ||
    !Array.isArray(data.plans)
  ) {
    throw new Error(
      "The plan changed. Reload the latest plan before confirming.",
    );
  }
  return data;
}

// Generated session data has no backend record to confirm a crew plan against.
const OFFLINE =
  "Crew confirmations are unavailable for generated onboarding data. Switch to the connected backend to record one.";

export function ApprovalProvider({
  incidents,
  source,
  children,
  readOnly = false,
  offline = false,
}) {
  const [entries, setEntries] = useState({});
  const [saving, setSaving] = useState({});
  const requests = useRef(new Map()),
    controllers = useRef(new Set()),
    mounted = useRef(true);
  const current = useRef(incidents);
  current.current = incidents;
  const receipts = useRef(new Map());
  const sequence = useRef(0);

  async function load(incident) {
    if (readOnly || offline) return;
    const key = contextKey(incident),
      serial = (requests.current.get(key) || 0) + 1;
    requests.current.set(key, serial);
    const controller = new AbortController();
    controllers.current.add(controller);
    const expected = requestFields(source, incident);
    try {
      const response = await fetch(
        `/api/crew-approvals?${new URLSearchParams(expected)}`,
        {
          cache: "no-store",
          signal: AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(10000),
          ]),
        },
      );
      const data = await responseData(response, expected);
      if (mounted.current && requests.current.get(key) === serial)
        setEntries((previous) => ({
          ...previous,
          [key]: { phase: "ready", data, error: null },
        }));
    } catch (error) {
      if (
        !controller.signal.aborted &&
        mounted.current &&
        requests.current.get(key) === serial
      )
        setEntries((previous) => ({
          ...previous,
          [key]: { phase: "error", error: error.message },
        }));
    } finally {
      controllers.current.delete(controller);
    }
  }
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      for (const controller of controllers.current) controller.abort();
    };
  }, []);
  useEffect(() => {
    if (readOnly || offline) return;
    for (const incident of incidents) void load(incident);
    const timer = setInterval(() => {
      for (const incident of current.current) void load(incident);
    }, 5000);
    return () => clearInterval(timer);
  }, [incidents, source, readOnly, offline]);

  function info(incident, teamId) {
    if (readOnly) return { phase: "historical", saving: false };
    if (offline) return { phase: "error", error: OFFLINE, saving: false };
    const entry = entries[contextKey(incident)] || { phase: "loading" };
    const plan = entry.data?.plans.find((p) => p.team_id === teamId);
    return {
      ...entry,
      plan,
      analyst: entry.data?.analyst,
      saving: !!saving[`${contextKey(incident)}:${teamId}`],
    };
  }
  async function confirm(incident, teamId, reviewedVersion) {
    if (readOnly)
      throw new Error("Return to current state before confirming a plan.");
    if (offline) throw new Error(OFFLINE);
    const key = contextKey(incident),
      saveKey = `${key}:${teamId}`;
    const entry = info(incident, teamId);
    if (
      entry.phase !== "ready" ||
      !entry.plan?.can_confirm ||
      entry.plan.plan_version !== reviewedVersion
    )
      throw new Error(
        "The plan changed. Reload the dashboard to review the latest plan before confirming.",
      );
    if (entry.plan.approval) return;
    if (saving[saveKey]) return;
    // Ignore earlier polling responses while this explicit mutation is in flight.
    const serial = (requests.current.get(key) || 0) + 1;
    requests.current.set(key, serial);
    setSaving((previous) => ({ ...previous, [saveKey]: true }));
    const controller = new AbortController();
    controllers.current.add(controller);
    const expected = requestFields(source, incident);
    try {
      const response = await fetch("/api/crew-approvals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...expected,
          team_id: teamId,
          plan_version: reviewedVersion,
        }),
        signal: AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(10000),
        ]),
      });
      const data = await responseData(response, expected);
      if (mounted.current) {
        requests.current.set(key, (requests.current.get(key) || 0) + 1);
        setEntries((previous) => ({
          ...previous,
          [key]: { phase: "ready", data, error: null },
        }));
      }
    } catch (error) {
      if (mounted.current) void load(incident);
      throw error;
    } finally {
      controllers.current.delete(controller);
      if (mounted.current)
        setSaving((previous) => ({ ...previous, [saveKey]: false }));
    }
  }

  const logIncidents = useMemo(() => {
    if (readOnly) return incidents;
    const receive = (incident, event, prefix) => {
      const key = `${incident.id}:${prefix}:${event.event_id || JSON.stringify(event)}`;
      if (!receipts.current.has(key))
        receipts.current.set(key, {
          ...event,
          event_id: key,
          incident_id: incident.id,
          received_at:
            prefix === "coordination" && source === "connected"
              ? event.received_at || new Date().toISOString()
              : new Date().toISOString(),
          ingestion_sequence: ++sequence.current,
          received_basis: "Browser session receipt",
        });
    };
    for (const incident of incidents)
      for (const event of incident.events || [])
        receive(incident, event, "coordination");
    for (const incident of incidents)
      for (const event of entries[contextKey(incident)]?.data?.events || [])
        receive(incident, event, "approval");
    return incidents.map((incident) => ({
      ...incident,
      events: [...receipts.current.values()].filter(
        (event) => event.incident_id === incident.id,
      ),
    }));
  }, [incidents, entries, source, readOnly, offline]);

  return (
    <ApprovalContext.Provider
      value={{ info, confirm, refresh: load, logIncidents }}
    >
      {children}
    </ApprovalContext.Provider>
  );
}
export function useApprovals() {
  const value = useContext(ApprovalContext);
  if (!value) throw new Error("Approval state is not available");
  return value;
}
