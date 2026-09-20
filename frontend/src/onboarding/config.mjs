// Demo onboarding input only. Nothing here is an official boundary, register or roster.
// The Catalonia box is an approximate envelope used to reject obvious typos, not the administrative border.
export const CATALONIA = {
  latMin: 40.5,
  latMax: 42.9,
  lonMin: 0.15,
  lonMax: 3.35,
  label: "Catalonia (approximate bounding box)",
};

// The two resource types the Resources page already shows (frontend/src/state/demo.mjs).
// Their capability tags come from the roster contract (CONTRACTS section 5 and
// fixtures/teams.json), so a generated plan never asks for a capability the system
// does not model: this project plans coordination work, not suppression.
export const RESOURCE_TYPES = [
  {
    id: "ground_crew",
    label: "Ground crew",
    capabilities: [
      "occupancy_check",
      "facility_contact",
      "assisted_evacuation",
    ],
    action: "confirm_occupancy",
  },
  {
    id: "fire_engine",
    label: "Fire engine",
    capabilities: ["access_check", "transport"],
    action: "check_access",
  },
];

// Default required capabilities per action, from the same contract.
export const ACTION_CAPABILITIES = {
  confirm_occupancy: ["occupancy_check"],
  contact_facility: ["facility_contact"],
  check_access: ["access_check"],
  request_resources: ["resource_request"],
  assisted_evacuation: ["assisted_evacuation"],
};

export const PLACEMENTS = [
  ["station", "At the fire station"],
  ["territory", "Random across the territory"],
];

// Approximate coordinates of well-known Catalan towns, used only to label a supplied
// point with its nearest reference place. Not a gazetteer lookup and not a boundary.
export const REFERENCE_PLACES = [
  ["Barcelona", 41.387, 2.17],
  ["Sabadell", 41.548, 2.108],
  ["Granollers", 41.608, 2.288],
  ["Mataró", 41.54, 2.445],
  ["Vic", 41.93, 2.255],
  ["Manresa", 41.726, 1.826],
  ["Igualada", 41.579, 1.617],
  ["Berga", 42.101, 1.845],
  ["Solsona", 41.994, 1.518],
  ["Ripoll", 42.201, 2.191],
  ["Olot", 42.182, 2.49],
  ["Banyoles", 42.119, 2.766],
  ["Figueres", 42.267, 2.961],
  ["Girona", 41.983, 2.824],
  ["La Bisbal d'Empordà", 41.96, 3.038],
  ["Palamós", 41.847, 3.129],
  ["Sant Feliu de Guíxols", 41.78, 3.028],
  ["Blanes", 41.674, 2.791],
  ["Santa Coloma de Farners", 41.86, 2.667],
  ["Puigcerdà", 42.432, 1.928],
  ["La Seu d'Urgell", 42.358, 1.459],
  ["Sort", 42.413, 1.13],
  ["Tremp", 42.167, 0.895],
  ["Vielha", 42.702, 0.796],
  ["Balaguer", 41.79, 0.807],
  ["Lleida", 41.617, 0.623],
  ["Mollerussa", 41.632, 0.895],
  ["Tàrrega", 41.647, 1.139],
  ["Cervera", 41.67, 1.272],
  ["Les Borges Blanques", 41.522, 0.868],
  ["Montblanc", 41.376, 1.161],
  ["Valls", 41.286, 1.25],
  ["Reus", 41.155, 1.107],
  ["Tarragona", 41.119, 1.245],
  ["Falset", 41.145, 0.82],
  ["Móra d'Ebre", 41.093, 0.643],
  ["Gandesa", 41.053, 0.436],
  ["Tortosa", 40.813, 0.521],
  ["Amposta", 40.708, 0.581],
  ["El Vendrell", 41.22, 1.535],
  ["Vilafranca del Penedès", 41.346, 1.698],
  ["Vilanova i la Geltrú", 41.224, 1.726],
];

export const withinCatalonia = (point) =>
  Number.isFinite(point?.latitude) &&
  Number.isFinite(point?.longitude) &&
  point.latitude >= CATALONIA.latMin &&
  point.latitude <= CATALONIA.latMax &&
  point.longitude >= CATALONIA.lonMin &&
  point.longitude <= CATALONIA.lonMax;

export function nearestPlace(point) {
  let best = null;
  for (const [name, latitude, longitude] of REFERENCE_PLACES) {
    // Degrees compared on a local scale; longitude is narrowed by latitude.
    const dy = point.latitude - latitude;
    const dx =
      (point.longitude - longitude) * Math.cos((latitude * Math.PI) / 180);
    const distance = Math.hypot(dy, dx);
    if (!best || distance < best.distance)
      best = { name, latitude, longitude, distance };
  }
  return best;
}

const COORDINATE = /^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$/;

/** One GPS pair per line. Each accepted pair becomes one fire. */
export function parseCoordinates(text) {
  const points = [],
    problems = [];
  for (const [index, raw] of String(text ?? "")
    .split(/\r?\n/)
    .entries()) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const match = COORDINATE.exec(line);
    if (!match) {
      problems.push({
        line: index + 1,
        raw: line,
        reason: "Not a latitude, longitude pair",
      });
      continue;
    }
    const point = { latitude: Number(match[1]), longitude: Number(match[2]) };
    if (!withinCatalonia(point)) {
      problems.push({
        line: index + 1,
        raw: line,
        reason: `Outside ${CATALONIA.label}`,
      });
      continue;
    }
    points.push({ ...point, line: index + 1 });
  }
  return { points, problems };
}

const DIGITS = /^\+?\d{6,15}$/;

/** One number per line. Each accepted number is one contact the voice agent would call. */
export function parsePhones(text) {
  const numbers = [],
    problems = [];
  const seen = new Set();
  for (const [index, raw] of String(text ?? "")
    .split(/\r?\n/)
    .entries()) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const compact = line.replace(/[\s().-]/g, "");
    if (!DIGITS.test(compact)) {
      problems.push({
        line: index + 1,
        raw: line,
        reason: "Not a dialable number",
      });
      continue;
    }
    if (seen.has(compact)) {
      problems.push({ line: index + 1, raw: line, reason: "Duplicate number" });
      continue;
    }
    seen.add(compact);
    numbers.push({ number: compact, entered: line, line: index + 1 });
  }
  return { numbers, problems };
}

// The call list and the resource roster are the jurisdiction's own facts: they start
// empty so nothing is generated from numbers or units nobody entered.
export function defaultDraft() {
  return {
    department: "Bombers de la Bisbal",
    fires: "41.9400, 3.0400\n41.8600, 2.9100",
    station: "41.9600, 3.0380",
    phones: "",
    resources: Object.fromEntries(
      RESOURCE_TYPES.map((type) => [
        type.id,
        { count: 0, placement: "station" },
      ]),
    ),
  };
}

const parsePoint = (text) => {
  const match = COORDINATE.exec(String(text ?? ""));
  if (!match) return null;
  const point = { latitude: Number(match[1]), longitude: Number(match[2]) };
  return withinCatalonia(point) ? point : null;
};

/** Canonical, storable configuration. Returns problems instead of throwing so the form can show them. */
export function buildConfig(draft, { createdAt } = {}) {
  const fires = parseCoordinates(draft.fires);
  const phones = parsePhones(draft.phones);
  const station = parsePoint(draft.station);
  const resources = RESOURCE_TYPES.map((type) => {
    const entry = draft.resources?.[type.id] || {};
    const count = Math.max(
      0,
      Math.min(40, Math.trunc(Number(entry.count) || 0)),
    );
    const placement = entry.placement === "territory" ? "territory" : "station";
    return { type: type.id, count, placement };
  }).filter((resource) => resource.count > 0);
  const department = String(draft.department ?? "").trim();
  // Each problem names the step that can fix it, so the form reports it in place.
  const problems = [];
  const problem = (step, message) => problems.push({ step, message });
  if (!department)
    problem("jurisdiction", "Give the jurisdiction's fire department a name.");
  if (String(draft.station ?? "").trim() && !station)
    problem(
      "jurisdiction",
      `The fire station pair is unreadable or outside ${CATALONIA.label}.`,
    );
  if (!fires.points.length)
    problem("fires", `Supply at least one GPS pair inside ${CATALONIA.label}.`);
  if (!phones.numbers.length)
    problem(
      "calls",
      "Supply at least one phone number for the voice agent to call.",
    );
  if (!resources.length)
    problem(
      "resources",
      "Set a resource count above zero for at least one type.",
    );
  if (
    !station &&
    resources.some((resource) => resource.placement === "station")
  )
    problem(
      "resources",
      "Supply the fire station pair on the jurisdiction step, or place every resource type across the territory.",
    );
  const config = {
    schema_version: "onboarding-demo-2",
    created_at: createdAt || new Date().toISOString(),
    department,
    station,
    fires: fires.points.map(({ latitude, longitude }) => ({
      latitude,
      longitude,
    })),
    phones: phones.numbers.map((entry) => entry.number),
    resources,
  };
  return { config, problems, fires, phones, station };
}

export const problemsFor = (problems, step) =>
  problems.filter((entry) => entry.step === step).map((entry) => entry.message);

export function summarise(config) {
  const resources = config.resources.reduce(
    (total, resource) => total + resource.count,
    0,
  );
  return {
    fires: config.fires.length,
    phones: config.phones.length,
    resources,
  };
}

/** Rejects anything that is not a configuration this build can generate a dashboard from. */
export function isConfig(value) {
  return (
    !!value &&
    value.schema_version === "onboarding-demo-2" &&
    typeof value.department === "string" &&
    value.department.length > 0 &&
    Array.isArray(value.fires) &&
    value.fires.length > 0 &&
    value.fires.every(withinCatalonia) &&
    Array.isArray(value.phones) &&
    value.phones.length > 0 &&
    value.phones.every(
      (phone) => typeof phone === "string" && phone.length > 0,
    ) &&
    Array.isArray(value.resources) &&
    value.resources.length > 0 &&
    value.resources.every(
      (resource) =>
        RESOURCE_TYPES.some((type) => type.id === resource.type) &&
        Number.isInteger(resource.count) &&
        resource.count > 0 &&
        ["station", "territory"].includes(resource.placement),
    ) &&
    (value.station === null || withinCatalonia(value.station)) &&
    (value.station !== null ||
      value.resources.every(
        (resource) => resource.placement === "territory",
      )) &&
    typeof value.created_at === "string" &&
    Number.isFinite(Date.parse(value.created_at))
  );
}

const pointText = (point) =>
  point ? `${point.latitude}, ${point.longitude}` : "";

/** Reopens a stored configuration in the form it was entered in. */
export function draftFromConfig(config) {
  return {
    department: config.department,
    fires: config.fires.map(pointText).join("\n"),
    station: pointText(config.station),
    phones: config.phones.join("\n"),
    resources: Object.fromEntries(
      RESOURCE_TYPES.map((type) => {
        const resource = config.resources.find(
          (entry) => entry.type === type.id,
        );
        return [
          type.id,
          {
            count: resource?.count ?? 0,
            placement: resource?.placement ?? "station",
          },
        ];
      }),
    ),
  };
}
