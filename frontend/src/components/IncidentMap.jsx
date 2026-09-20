import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useNavigate } from "react-router-dom";
import { responseTeams, gps } from "../state/model.mjs";
import { crewMapData } from "../state/crew-map.mjs";
import MapFrame from "./MapFrame";
import { riskHeatPoints } from "../state/risk-heat.mjs";
import { riskHeatLayer } from "./risk-heat-layer";
export default function IncidentMap({
  incidents,
  showRoutes = false,
  openIncidentOnClick = false,
  interactive = true,
}) {
  const [heat, setHeat] = useState(false);
  const heatPoints = riskHeatPoints(incidents);
  const host = useRef(null),
    mapRef = useRef(null),
    layersRef = useRef(null),
    fittedScope = useRef(null),
    navigate = useNavigate();
  useEffect(() => {
    const map = L.map(host.current, {
      scrollWheelZoom: false,
      zoomAnimation: false,
      fadeAnimation: false,
      zoomControl: false,
      attributionControl: false,
    }).setView([41.99, 2.9], 9);
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Esri World Imagery · reference basemap", maxZoom: 18 },
    ).addTo(map);
    mapRef.current = map;
    layersRef.current = L.featureGroup().addTo(map);
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
      fittedScope.current = null;
    };
  }, []);
  useEffect(() => {
    const map = mapRef.current,
      layers = layersRef.current;
    if (!map || !layers) return;
    layers.clearLayers();
    const bounds = [];
    for (const incident of incidents) {
      if (incident.fire_geometry) {
        try {
          const halo = L.geoJSON(incident.fire_geometry, {
            color: "#fff",
            weight: 5,
            fill: false,
          }).addTo(layers);
          const perimeter = L.geoJSON(incident.fire_geometry, {
            color: "#ff603e",
            weight: 2,
            fillColor: "#ff603e",
            fillOpacity: 0.3,
          }).addTo(layers);
          if (halo.getBounds().isValid()) {
            bounds.push(halo.getBounds());
            if (openIncidentOnClick) {
              const open = () =>
                navigate(
                  `/incidents/${encodeURIComponent(incident.id)}/summary`,
                );
              perimeter.on("click", open);
              const label = document.createElement("span");
              label.textContent = incident.name;
              perimeter.bindTooltip(label);
              halo.on("click", open);
              const marker = L.marker(halo.getBounds().getCenter(), {
                title: `Open incident ${incident.name}`,
                keyboard: true,
                zIndexOffset: 1000,
                icon: L.divIcon({
                  className: "fire-map-pin",
                  iconSize: [44, 44],
                  iconAnchor: [22, 22],
                  html: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M13 2c1 5-3 5-2 9 1-1 2-2 2-4 4 3 6 6 5 10-1 3-3 5-6 5-4 0-7-3-7-7 0-4 3-6 4-9 0 3 1 4 2 4-1-4 2-5 2-8Z"/></svg>',
                }),
              })
                .addTo(layers)
                .on("click", open);
              marker
                .getElement()
                ?.setAttribute("aria-label", `Open incident ${incident.name}`);
            }
          }
        } catch {
          /* Invalid supplied geometry must not hide the location list. */
        }
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
        label.textContent = `${asset.name} · GPS: ${gps(asset)} (lat, lon)`;
        const contact = incident.contacts.ranked?.find(
          (c) => c.asset_id === asset.asset_id,
        );
        const colour =
          asset.risk_label === "High" || contact?.status === "window_exhausted"
            ? "#ff603e"
            : "#1677ff";
        const marker = L.circleMarker(position, {
          radius: 7,
          color: "#fff",
          weight: 2,
          fillColor: colour,
          fillOpacity: 1,
        })
          .bindTooltip(label)
          .addTo(layers);
        // A preview of unsaved incidents has nothing to open.
        if (interactive)
          marker.on("click", () =>
            navigate(
              `/incidents/${encodeURIComponent(incident.id)}/buildings?building=${encodeURIComponent(asset.asset_id)}`,
            ),
          );
      }
      if (showRoutes) {
        const crew = responseTeams(incident).flatMap((team) =>
          crewMapData(team, incident.assets).stops.map((stop) => ({
            path: stop.path,
          })),
        );
        const evacuation = (incident.plan.locations || []).flatMap(
          (l) => l.routes || [],
        );
        for (const route of [...crew, ...evacuation]) {
          if (
            route.path?.length > 1 &&
            route.path.every((p) => p.length === 2 && p.every(Number.isFinite))
          )
            L.polyline(route.path, {
              color: "#69b1ff",
              weight: 3,
              dashArray: route.status === "confirmed" ? null : "8 7",
            }).addTo(layers);
        }
      }
    }
    const scope = incidents.map((i) => i.id).join("|");
    if (bounds.length && fittedScope.current !== scope) {
      map.fitBounds(L.latLngBounds(bounds), {
        padding: [45, 45],
        maxZoom: 13,
        animate: false,
      });
      fittedScope.current = scope;
    }
  }, [incidents, showRoutes, openIncidentOnClick, interactive, navigate]);
  useEffect(() => {
    if (!heat || !mapRef.current) return;
    const layer = riskHeatLayer(riskHeatPoints(incidents)).addTo(
      mapRef.current,
    );
    return () => layer.remove();
  }, [heat, incidents]);
  return (
    <div className="map-wrap">
      <MapFrame
        mapRef={mapRef}
        heat={heat}
        onHeatChange={setHeat}
        heatCount={heatPoints.length}
      >
        <div
          ref={host}
          className="incident-map"
          aria-label="Incident and location map"
        />
      </MapFrame>
      <div className="map-legend">
        <span>
          <i className="dot fire" />
          Fire perimeter
        </span>
        <span>
          <i className="dot blue" />
          Identified location
        </span>
        {showRoutes && <span>Dashed: planned route</span>}
      </div>
    </div>
  );
}
