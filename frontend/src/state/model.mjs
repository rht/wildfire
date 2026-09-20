/** Presentation only: backend order, risk and operational facts remain authoritative. */
export const number = (value) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
export const humanize = (value) =>
  value
    ? String(value)
        .replaceAll("_", " ")
        .replace(/^./, (c) => c.toUpperCase())
    : "Not supplied";
export const count = (value) =>
  number(value) === null ? "—" : new Intl.NumberFormat("en-GB").format(value);
export const money = (value) =>
  number(value) === null
    ? "—"
    : new Intl.NumberFormat("en-GB", {
        style: "currency",
        currency: "EUR",
        maximumFractionDigits: 0,
      }).format(value);
export const stamp = (value) =>
  value && Number.isFinite(Date.parse(value))
    ? new Date(value).toISOString().replace("T", " ").replace(".000Z", " UTC")
    : "Not supplied";
export function toIncident(state) {
  return {
    ...state,
    id: state.incident_id || state.scenario_id,
    name: state.name || humanize(state.scenario_id),
    status: state.incident_status ?? null,
    as_of: state.snapshot_as_of || state.as_of,
    assets: state.assets || [],
    teams: state.teams || [],
    calls: state.calls || [],
    events: state.events || [],
    contacts: state.contacts || { ranked: [], review: [] },
    plan: state.plan || { locations: [], response: null },
    peopleClusters: state.peopleClusters ?? null,
    resources: state.resources ?? null,
  };
}
export function metrics(incidents) {
  const known = incidents.length > 0;
  return {
    active:
      known && incidents.every((i) => i.status !== null)
        ? incidents.filter((i) => i.status === "active").length
        : null,
    deployed:
      known && incidents.every((i) => Array.isArray(i.resources))
        ? new Set(
            incidents.flatMap((i) =>
              i.resources
                .filter((r) => r.status === "deployed")
                .map((r) => r.id),
            ),
          ).size
        : null,
    structures: new Set(
      incidents.flatMap((i) => i.assets.map((a) => a.asset_id)),
    ).size,
    clusters:
      known && incidents.every((i) => Array.isArray(i.peopleClusters))
        ? new Set(incidents.flatMap((i) => i.peopleClusters.map((g) => g.id)))
            .size
        : null,
  };
}
export function callRows(incident) {
  const ordered = [
    ...(incident.contacts.ranked || []),
    ...(incident.contacts.review || []),
  ];
  const seen = new Set();
  const entries = [
    ...ordered,
    ...incident.assets.map((a) => ({ asset_id: a.asset_id })),
  ].filter((c) => !seen.has(c.asset_id) && seen.add(c.asset_id));
  return entries.map((c) => {
    const asset = incident.assets.find((a) => a.asset_id === c.asset_id) || {};
    const calls = incident.calls.filter((r) => r.asset_id === c.asset_id);
    const latest = [...calls]
      .sort(
        (a, b) =>
          (Date.parse(a.observed_at) || 0) - (Date.parse(b.observed_at) || 0),
      )
      .at(-1);
    const location = incident.plan.locations?.find(
      (l) => l.asset_id === c.asset_id,
    );
    const reasons = new Set(
      calls.flatMap((r) => r.human_followup_reasons || []).map(humanize),
    );
    for (const r of calls) {
      if (r.wants_human === true) reasons.add("Human requested");
      if ((r.reported_needs_assistance ?? r.needs_assistance) === true)
        reasons.add("Assistance needed");
      if (r.bad_audio === true) reasons.add("Audio unclear");
      if (r.contradictory === true) reasons.add("Contradictory answers");
      if (["failed", "no_answer", "busy"].includes(r.status))
        reasons.add(humanize(r.status));
    }
    if (location?.human_followup && calls.length)
      for (const reason of location.reasons || [])
        reasons.add(humanize(reason));
    const followup =
      calls.some((r) => r.human_followup_required === true) || reasons.size > 0;
    if (followup && !reasons.size) reasons.add("Human review required");
    const called = calls.some((r) => r.status === "completed");
    return {
      ...asset,
      ...c,
      calls,
      latest,
      called,
      toCall: !called,
      followup,
      reasons: [...reasons],
    };
  });
}
export function buildingRows(incidents) {
  return incidents.flatMap((i) =>
    i.assets.map((a) => ({
      ...a,
      key: `${i.id}:${a.asset_id}`,
      incident_id: i.id,
      incident_name: i.name,
      assessed_at: a.assessed_at || i.as_of,
      risk_score: number(a.risk_score),
      contact: i.contacts.ranked?.find((c) => c.asset_id === a.asset_id),
      risk_label:
        a.risk_label ||
        (a.intersects_fire === true
          ? "Fire intersects"
          : number(a.burn_probability) !== null
            ? "Forecast available"
            : "Not assessed"),
    })),
  );
}
export function withinDate(value, from, to) {
  const date = value?.slice(0, 10);
  return (!from || (date && date >= from)) && (!to || (date && date <= to));
}
export function filterBuildings(
  rows,
  { incident = "", from = "", to = "", search = "", type = "", risk = "" } = {},
) {
  return rows.filter(
    (r) =>
      (!incident || r.incident_id === incident) &&
      withinDate(r.assessed_at, from, to) &&
      (!type || r.asset_type === type) &&
      (!risk || r.risk_label === risk) &&
      (!search ||
        `${r.name} ${r.municipality || ""} ${r.asset_id}`
          .toLowerCase()
          .includes(search.toLowerCase())),
  );
}
export function orderedEvents(
  incidents,
  {
    incident = "",
    from = "",
    to = "",
    kind = "",
    severity = "",
    order = "newest",
  } = {},
) {
  const rows = incidents.flatMap((i) =>
    i.events.map((e, index) => ({
      ...e,
      incident_id: i.id,
      incident_name: i.name,
      key: `${i.id}:${e.event_id || index}`,
      source_order: index,
    })),
  );
  return rows
    .filter(
      (e) =>
        (!incident || e.incident_id === incident) &&
        (!kind || e.kind === kind) &&
        (!severity || e.severity === severity) &&
        withinDate(e.received_at || e.as_of, from, to),
    )
    .sort((a, b) => {
      const time =
        (Date.parse(a.received_at) || 0) - (Date.parse(b.received_at) || 0);
      const sequence =
        (number(a.ingestion_sequence) ?? a.source_order) -
        (number(b.ingestion_sequence) ?? b.source_order);
      return (
        (time || sequence || a.key.localeCompare(b.key)) *
        (order === "oldest" ? 1 : -1)
      );
    });
}
export function evacuationTotals(incident) {
  if (!Array.isArray(incident.peopleClusters))
    return {
      selfEvacuating: null,
      assistance: null,
      unknown: null,
      arrived: null,
    };
  const total = (status) => {
    const groups = incident.peopleClusters.filter((g) => g.status === status);
    return groups.some((g) => number(g.people) === null)
      ? null
      : groups.reduce((n, g) => n + g.people, 0);
  };
  return {
    selfEvacuating: total("self_evacuating"),
    assistance: total("needs_assistance"),
    unknown: total("unknown"),
    arrived: total("arrived"),
  };
}
export function responseTeams(incident) {
  const r = incident.plan.response;
  if (!r) return [];
  return (
    r.teams || [
      {
        team_id: "Unspecified crew",
        tasks: (r.steps || []).map((s) => ({
          ...s,
          asset_id: s.asset_id || s.site_id,
          status: "proposed",
        })),
      },
    ]
  );
}
