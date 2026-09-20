// Risk scores are supplied assessments, not temperatures or inferred fire intensity.
export function riskHeatPoints(incidents) {
  return incidents.flatMap((incident) =>
    (incident.assets || []).flatMap((asset) => {
      const { latitude, longitude, risk_score: score } = asset;
      return Number.isFinite(latitude) &&
        Math.abs(latitude) <= 90 &&
        Number.isFinite(longitude) &&
        Math.abs(longitude) <= 180 &&
        Number.isFinite(score) &&
        score >= 0 &&
        score <= 100
        ? [{ latitude, longitude, score }]
        : [];
    }),
  );
}

export function riskHeatColour(score) {
  const stops = [
    [36, 109, 186],
    [249, 199, 79],
    [194, 34, 37],
  ];
  const position = Math.max(0, Math.min(100, score)) / 50;
  const index = Math.min(1, Math.floor(position));
  return stops[index].map((channel, i) =>
    Math.round(channel + (stops[index + 1][i] - channel) * (position - index)),
  );
}
