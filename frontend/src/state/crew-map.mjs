// Supplied plan geometry only. Never infer crew movement from a route or start_node.
const position = (point) =>
  Number.isFinite(point?.latitude) &&
  Number.isFinite(point?.longitude) &&
  Math.abs(point.latitude) <= 90 &&
  Math.abs(point.longitude) <= 180
    ? [point.latitude, point.longitude]
    : null;

export const crewStopId = (task, index) =>
  task.action_id || `__stop__:${task.asset_id || "unknown"}:${index}`;

// Arrow anchors lie on supplied segments; they never bridge missing route legs.
export function crewRouteArrows(path) {
  const arrows = [];
  for (let index = 1; index < path.length; index += 1) {
    const [lat1, lon1] = path[index - 1];
    const [lat2, lon2] = path[index];
    if (lat1 === lat2 && lon1 === lon2) continue;
    const mercatorY = (lat) =>
      Math.log(
        Math.tan(
          Math.PI / 4 + (Math.min(85, Math.max(-85, lat)) * Math.PI) / 360,
        ),
      );
    const rotation =
      (Math.atan2(
        mercatorY(lat1) - mercatorY(lat2),
        ((lon2 - lon1) * Math.PI) / 180,
      ) *
        180) /
      Math.PI;
    arrows.push({ position: [(lat1 + lat2) / 2, (lon1 + lon2) / 2], rotation });
  }
  // Keep long detailed road geometries legible at the crew overview scale.
  return arrows.filter(
    (_, index) => index % Math.max(1, Math.ceil(arrows.length / 12)) === 0,
  );
}

export function crewMapData(team = {}, assets = []) {
  const currentPosition = position(team.current_location);
  const plannedStart = team.starting_location ?? team.planning_context?.start;
  const plannedPosition = position(plannedStart);
  const startLocation = plannedPosition ? plannedStart : team.current_location;
  const startPosition = plannedPosition || currentPosition;
  return {
    start: startPosition
      ? {
          ...startLocation,
          id: "__start__",
          name:
            startLocation.name ||
            (plannedPosition
              ? "Planned starting point"
              : "Reported crew location"),
          kind: plannedPosition ? "planned" : "reported",
          position: startPosition,
        }
      : null,
    current: currentPosition
      ? { ...team.current_location, position: currentPosition }
      : null,
    stops: (team.tasks || []).map((task, index) => {
      const asset = assets.find((item) => item.asset_id === task.asset_id);
      const supplied = task.path_lonlat;
      const validPath =
        Array.isArray(supplied) &&
        supplied.length > 1 &&
        supplied.every(
          (point) =>
            Array.isArray(point) &&
            point.length === 2 &&
            position({ latitude: point[1], longitude: point[0] }),
        );
      const path = validPath ? supplied.map(([lon, lat]) => [lat, lon]) : [];
      const destination = position(asset);
      return {
        id: crewStopId(task, index),
        number: index + 1,
        name: asset?.name || task.asset_id || "Destination not supplied",
        position: destination || path.at(-1) || null,
        positionSource: destination
          ? "Identified location"
          : "Supplied route endpoint",
        path,
      };
    }),
  };
}
