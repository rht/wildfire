// Illustrative fire outline for the Design demo. Not an observation or a forecast.
// The outline is the radial envelope of a recorded Gavarres satellite perimeter
// around its vertex centre, normalised so the longest axis spans one unit. Being
// star-shaped from the origin, scaling it about the origin keeps successive
// timeline stages nested (see scripts/generate-history.mjs).
const OUTLINE = [
  [0.9515, 0],
  [0.9149, 0.1205],
  [0.8166, 0.2188],
  [0.7322, 0.3033],
  [0.6442, 0.3719],
  [0.4502, 0.3455],
  [0.4204, 0.4204],
  [0.3853, 0.5021],
  [0.3413, 0.5911],
  [0.2693, 0.6501],
  [0.1866, 0.6963],
  [0.1096, 0.8324],
  [0, 1],
  [-0.1078, 0.8189],
  [-0.1893, 0.7065],
  [-0.2801, 0.6762],
  [-0.3726, 0.6454],
  [-0.1496, 0.1949],
  [-0.1718, 0.1718],
  [-0.2034, 0.156],
  [-0.2392, 0.1381],
  [-0.4723, 0.1956],
  [-0.5003, 0.1341],
  [-0.5023, 0.0661],
  [-0.4484, 0],
  [-0.4154, -0.0547],
  [-0.4154, -0.1113],
  [-0.5888, -0.2439],
  [-0.5905, -0.3409],
  [-0.593, -0.455],
  [-0.679, -0.679],
  [-0.6185, -0.806],
  [-0.5085, -0.8808],
  [-0.3893, -0.94],
  [-0.2321, -0.866],
  [-0.1135, -0.8622],
  [0, -0.8309],
  [0.1027, -0.7802],
  [0.2074, -0.7741],
  [0.3135, -0.7568],
  [0.3757, -0.6508],
  [0.4593, -0.5986],
  [0.5818, -0.5818],
  [0.6327, -0.4855],
  [0.6814, -0.3934],
  [0.715, -0.2962],
  [0.8462, -0.2267],
  [0.9043, -0.1191],
];
// Approximate footprint of the demo fire in degrees, and where it sits relative
// to each incident centre so the illustrative assets stay outside the perimeter.
const HALF_SPAN_DEG = 0.021;
const OFFSET = { lon: 0.012, lat: 0.02 };
const round = (value) => Number(value.toFixed(6));
// One closed GeoJSON polygon per incident, rotated so incidents do not share a
// silhouette. centre is [lat, lon]; the rotation is in the illustrative degree
// plane, which is fine for a drawing that is never measured.
export function demoPerimeter(centre, turn = 0) {
  const angle = (turn * 2 * Math.PI) / 3;
  const cos = Math.cos(angle),
    sin = Math.sin(angle);
  const ring = OUTLINE.map(([x, y]) => [
    round(centre[1] + OFFSET.lon + (x * cos - y * sin) * HALF_SPAN_DEG),
    round(centre[0] + OFFSET.lat + (x * sin + y * cos) * HALF_SPAN_DEG),
  ]);
  ring.push([...ring[0]]);
  return { type: "Polygon", coordinates: [ring] };
}
