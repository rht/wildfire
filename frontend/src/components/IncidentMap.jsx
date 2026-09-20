import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useNavigate } from "react-router-dom";
export default function IncidentMap({ incidents, showRoutes = false }) {
  const host = useRef(null),
    navigate = useNavigate();
  useEffect(() => {
    const map = L.map(host.current, { scrollWheelZoom: false }).setView(
      [41.99, 2.9],
      9,
    );
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Esri World Imagery · reference basemap", maxZoom: 18 },
    ).addTo(map);
    const bounds = [];
    for (const incident of incidents) {
      if (incident.fire_geometry) {
        const halo = L.geoJSON(incident.fire_geometry, {
          color: "#fff",
          weight: 5,
          fill: false,
        }).addTo(map);
        L.geoJSON(incident.fire_geometry, {
          color: "#ff603e",
          weight: 2,
          fillColor: "#ff603e",
          fillOpacity: 0.3,
        }).addTo(map);
        if (halo.getBounds().isValid()) bounds.push(halo.getBounds());
      }
      for (const asset of incident.assets) {
        if (
          !Number.isFinite(asset.latitude) ||
          !Number.isFinite(asset.longitude)
        )
          continue;
        const position = [asset.latitude, asset.longitude];
        bounds.push(position);
        const label = document.createElement("span");
        label.textContent = asset.name;
        const colour = asset.risk_label === "High" ? "#ff603e" : "#1677ff";
        L.circleMarker(position, {
          radius: 7,
          color: "#fff",
          weight: 2,
          fillColor: colour,
          fillOpacity: 1,
        })
          .bindTooltip(label)
          .on("click", () =>
            navigate(
              `/incidents/${encodeURIComponent(incident.id)}/buildings?building=${encodeURIComponent(asset.asset_id)}`,
            ),
          )
          .addTo(map);
      }
      if (showRoutes)
        for (const team of incident.plan.response?.teams || [])
          for (const task of team.tasks || []) {
            if (
              task.path_lonlat?.length > 1 &&
              task.path_lonlat.every(
                (p) => p.length === 2 && p.every(Number.isFinite),
              )
            )
              L.polyline(
                task.path_lonlat.map(([lon, lat]) => [lat, lon]),
                { color: "#69b1ff", weight: 4, dashArray: "8 7" },
              ).addTo(map);
          }
    }
    if (bounds.length)
      map.fitBounds(L.latLngBounds(bounds), { padding: [45, 45], maxZoom: 13 });
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      map.remove();
    };
  }, [incidents, showRoutes, navigate]);
  return (
    <div className="map-wrap">
      <div
        ref={host}
        className="incident-map"
        aria-label="Incident and location map"
      />
      <div className="map-legend">
        <span>
          <i className="dot fire" />
          Fire perimeter
        </span>
        <span>
          <i className="dot blue" />
          Identified location
        </span>
        {showRoutes && <span>Dashed: proposed route</span>}
      </div>
    </div>
  );
}
