import { useEffect, useMemo, useRef } from "react";
import { Typography } from "@mui/material";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { crewMapData } from "../state/crew-map.mjs";
import { gps, stamp } from "../state/model.mjs";
import MapFrame from "./MapFrame";

export default function CrewPlanMap({ team, assets, name }) {
  const host = useRef(null);
  const mapRef = useRef(null);
  const data = useMemo(() => crewMapData(team, assets), [team, assets]);
  const hasPosition = data.current || data.stops.some((stop) => stop.position);
  useEffect(() => {
    if (!host.current) return;
    const map = L.map(host.current, {
      scrollWheelZoom: false,
      zoomAnimation: false,
      fadeAnimation: false,
      zoomControl: false,
      attributionControl: false,
    });
    mapRef.current = map;
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Esri World Imagery · reference basemap", maxZoom: 18 },
    ).addTo(map);
    const bounds = [];
    const destinations = new Map();
    for (const stop of data.stops) {
      if (stop.path.length) {
        L.polyline(stop.path, {
          color: "#69b1ff",
          weight: 4,
          dashArray: "8 7",
        }).addTo(map);
        bounds.push(...stop.path);
      }
      if (!stop.position) continue;
      const key = stop.position.join(",");
      if (!destinations.has(key)) destinations.set(key, []);
      destinations.get(key).push(stop);
      bounds.push(stop.position);
    }
    for (const stops of destinations.values()) {
      const numbers = stops.map((stop) => stop.number).join(" · ");
      const label = document.createElement("span");
      label.textContent = stops
        .map(
          (stop) =>
            `${stop.number}. ${stop.name} · GPS: ${stop.position.join(", ")} · ${stop.positionSource}`,
        )
        .join(" / ");
      const marker = L.marker(stops[0].position, {
        title: `Planned stop ${numbers}`,
        icon: L.divIcon({
          className: "crew-stop-pin",
          html: `<span>${numbers}</span>`,
          iconSize: [Math.max(32, numbers.length * 10), 32],
          iconAnchor: [16, 16],
        }),
      })
        .addTo(map)
        .bindTooltip(label);
      marker
        .getElement()
        ?.setAttribute("aria-label", `Planned stop ${numbers}`);
    }
    if (data.current) {
      bounds.push(data.current.position);
      const label = document.createElement("span");
      label.textContent = `${name} · Reported crew location · GPS: ${gps(data.current)} · ${stamp(data.current.observed_at)} · Source: ${data.current.source || "Not supplied"}`;
      const marker = L.marker(data.current.position, {
        title: "Reported crew location",
        zIndexOffset: 1000,
        icon: L.divIcon({
          className: "crew-position-pin",
          html: '<span aria-hidden="true">C</span>',
          iconSize: [28, 28],
          iconAnchor: [14, 32],
        }),
      })
        .addTo(map)
        .bindTooltip(label);
      marker.getElement()?.setAttribute("aria-label", "Reported crew location");
    }
    map.fitBounds(L.latLngBounds(bounds), {
      padding: [40, 40],
      maxZoom: 15,
      animate: false,
    });
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, [data, name]);
  const missingPaths = data.stops
    .filter((stop) => !stop.path.length)
    .map((stop) => stop.number);
  const missingStops = data.stops
    .filter((stop) => !stop.position)
    .map((stop) => stop.number);
  return (
    <section className="crew-route-review" aria-label="Crew route review">
      <Typography variant="h5" sx={{ mb: 1 }}>
        Planned route
      </Typography>
      {hasPosition ? (
        <MapFrame mapRef={mapRef}>
          <div
            ref={host}
            className="crew-route-map"
            aria-label={`Plan map for ${name}`}
          />
        </MapFrame>
      ) : (
        <Typography color="text.secondary">
          No map coordinates supplied.
        </Typography>
      )}
      <div className="map-legend">
        <span>Numbered stops: plan order</span>
        <span>Dashed: planned path, not recorded movement</span>
        {data.current && <span>C: reported crew location</span>}
      </div>
      <Typography variant="body2" sx={{ mt: 1 }}>
        {data.current
          ? `Reported crew location · GPS: ${gps(data.current)} · Observed: ${stamp(data.current.observed_at)} · Source: ${data.current.source || "Not supplied"}`
          : "Current crew location not supplied."}
      </Typography>
      {!!missingPaths.length && (
        <Typography variant="body2" color="text.secondary">
          Path not supplied for stops: {missingPaths.join(", ")}.
        </Typography>
      )}
      {!!missingStops.length && (
        <Typography variant="body2" color="text.secondary">
          Coordinates not supplied for stops: {missingStops.join(", ")}.
        </Typography>
      )}
    </section>
  );
}
