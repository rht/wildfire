// Supplied plan geometry only. Never infer crew movement from a route or start_node.
const position = (point) =>
  Number.isFinite(point?.latitude) &&
  Number.isFinite(point?.longitude) &&
  Math.abs(point.latitude) <= 90 &&
  Math.abs(point.longitude) <= 180
    ? [point.latitude, point.longitude]
    : null;

export function crewMapData(team = {}, assets = []) {
  const currentPosition = position(team.current_location);
  return {
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
