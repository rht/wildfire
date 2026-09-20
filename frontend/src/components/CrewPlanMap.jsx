import { useEffect, useMemo, useRef } from "react";
import { Typography } from "@mui/material";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  crewMapData,
  crewMapsData,
  crewRouteArrows,
} from "../state/crew-map.mjs";
import { gps, stamp } from "../state/model.mjs";
import MapFrame from "./MapFrame";

const textLabel = (text) => {
  const label = document.createElement("span");
  label.textContent = text;
  return label;
};

export default function CrewPlanMap({
  team,
  teams,
  assets,
  name,
  activeStopId = null,
  onActiveStopChange,
}) {
  const host = useRef(null);
  const mapRef = useRef(null);
  const layersRef = useRef([]);
  const callbackRef = useRef(onActiveStopChange);
  const data = useMemo(
    () => (teams ? crewMapsData(teams, assets) : crewMapData(team, assets)),
    [team, teams, assets],
  );
  const hasPosition =
    data.start ||
    data.current ||
    data.starts?.length ||
    data.currentLocations?.length ||
    data.stops.some((stop) => stop.position);
  useEffect(() => {
    callbackRef.current = onActiveStopChange;
  }, [onActiveStopChange]);
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
    const layers = [];
    layersRef.current = layers;
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Esri World Imagery · reference basemap", maxZoom: 18 },
    ).addTo(map);
    const bounds = [];
    const destinations = new Map();
    const bindSelection = (element, id, marker, label) => {
      const activate = () => {
        marker.setTooltipContent(textLabel(label)).openTooltip();
        callbackRef.current?.(id);
      };
      const deactivate = () => callbackRef.current?.(null);
      element.addEventListener("mouseenter", activate);
      element.addEventListener("focus", activate);
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        activate();
      });
      element.addEventListener("mouseleave", deactivate);
      element.addEventListener("blur", deactivate);
    };
    for (const stop of data.stops) {
      if (stop.path.length) {
        const color = stop.color || "#69b1ff";
        const line = L.polyline(stop.path, {
          color,
          weight: 4,
          dashArray: "8 7",
          dashOffset: String((stop.crewIndex || 0) * 8),
        }).addTo(map);
        line.bindTooltip(
          textLabel(
            `${stop.crewName ? `${stop.crewName} · ` : ""}To stop ${stop.number}: ${stop.name} · Supplied planned path`,
          ),
        );
        line.on("mouseover click", () => callbackRef.current?.(stop.id));
        line.on("mouseout", () => callbackRef.current?.(null));
        const arrows = crewRouteArrows(stop.path).map((arrow) => {
          const glyph = document.createElement("span");
          glyph.setAttribute("aria-hidden", "true");
          Object.assign(glyph.style, {
            display: "block",
            width: "0",
            height: "0",
            borderTop: "6px solid transparent",
            borderBottom: "6px solid transparent",
            borderLeft: "11px solid currentColor",
            transform: `rotate(${arrow.rotation}deg)`,
          });
          return L.marker(arrow.position, {
            interactive: false,
            keyboard: false,
            icon: L.divIcon({
              className: "crew-route-arrow",
              html: glyph,
              iconSize: [12, 12],
              iconAnchor: [6, 6],
            }),
          }).addTo(map);
        });
        layers.push({ ids: [stop.id], line, arrows, color });
        bounds.push(...stop.path);
      }
      if (!stop.position || data.pointGroups) continue;
      const key = stop.position.join(",");
      if (!destinations.has(key)) destinations.set(key, []);
      destinations.get(key).push(stop);
      bounds.push(stop.position);
    }
    for (const group of data.pointGroups || []) {
      destinations.set(group[0].position.join(","), group);
      bounds.push(group[0].position);
    }
    for (const stops of destinations.values()) {
      const symbol = (stop) =>
        stop.pointKind && stop.pointKind !== "stop"
          ? stop.kind === "planned"
            ? "S"
            : "C"
          : stop.number;
      const numbers = stops.map(symbol).join(" · ");
      const labels = stops.map((stop) => {
        const prefix = stop.crewName ? `${stop.crewName} · ` : "";
        const coordinates = gps({
          latitude: stop.position[0],
          longitude: stop.position[1],
        });
        if (stop.pointKind && stop.pointKind !== "stop")
          return `${prefix}${symbol(stop)}. ${stop.name} · ${stop.kind === "planned" ? "Planned starting point" : "Reported crew location"} · GPS: ${coordinates} · ${stop.kind === "planned" ? "Timestamp" : "Observed"}: ${stamp(stop.observed_at)} · Source: ${stop.source || "Not supplied"}`;
        return `${prefix}${stop.number}. ${stop.name} · GPS: ${coordinates} · ${stop.positionSource}`;
      });
      const content = document.createElement("span");
      const buttons = stops.map((stop, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = symbol(stop);
        if (stop.color) {
          button.style.backgroundColor = stop.color;
          button.style.color = "#102332";
        }
        if (stop.pointKind === "start") button.className = "crew-start-button";
        button.title = labels[index];
        button.setAttribute("aria-label", labels[index]);
        button.dataset.stopId = stop.id;
        if (stop.crewId) button.dataset.crewId = stop.crewId;
        content.append(button);
        return button;
      });
      const marker = L.marker(stops[0].position, {
        title: `Planned stop ${numbers}`,
        keyboard: false,
        icon: L.divIcon({
          className: "crew-stop-pin",
          html: content,
          iconSize: [Math.max(32, stops.length * 32), 32],
          iconAnchor: [16, 16],
        }),
      })
        .addTo(map)
        .bindTooltip(textLabel(labels.join(" / ")));
      marker.getElement()?.setAttribute("role", "group");
      marker
        .getElement()
        ?.setAttribute("aria-label", `Planned stop ${numbers}`);
      buttons.forEach((button, index) =>
        bindSelection(
          button,
          stops[index].pointKind === "current" ? null : stops[index].id,
          marker,
          labels[index],
        ),
      );
      layers.push({
        ids: stops.map((stop) => stop.id),
        marker,
        buttons,
        labels,
      });
    }
    if (data.start) {
      const planned = data.start.kind === "planned";
      const label = `${data.start.name} · ${planned ? "Planned starting point" : "Reported crew location"} · GPS: ${gps(data.start)} · ${planned ? "Timestamp" : "Observed"}: ${stamp(data.start.observed_at)} · Source: ${data.start.source || "Not supplied"}`;
      const marker = L.marker(data.start.position, {
        title: label,
        zIndexOffset: 1000,
        icon: L.divIcon({
          className: `crew-start-pin ${planned ? "" : "crew-position-pin"}`,
          html: `<span aria-hidden="true">${planned ? "S" : "C"}</span>`,
          iconSize: [28, 28],
          iconAnchor: [14, 42],
        }),
      })
        .addTo(map)
        .bindTooltip(textLabel(label));
      const element = marker.getElement();
      element?.setAttribute("aria-label", label);
      element?.setAttribute("role", "button");
      element?.setAttribute("data-stop-id", "__start__");
      if (element) {
        bindSelection(element, "__start__", marker, label);
        element.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            callbackRef.current?.("__start__");
          }
        });
      }
      layers.push({ ids: ["__start__"], marker, labels: [label] });
      bounds.push(data.start.position);
    }
    if (data.current && data.start?.kind === "planned") {
      bounds.push(data.current.position);
      const label = `${name} · Reported crew location · GPS: ${gps(data.current)} · ${stamp(data.current.observed_at)} · Source: ${data.current.source || "Not supplied"}`;
      const marker = L.marker(data.current.position, {
        title: "Reported crew location",
        zIndexOffset: 1000,
        icon: L.divIcon({
          className: "crew-position-pin",
          html: '<span aria-hidden="true">C</span>',
          iconSize: [28, 28],
          iconAnchor: [-16, 32],
        }),
      })
        .addTo(map)
        .bindTooltip(textLabel(label));
      marker.getElement()?.setAttribute("aria-label", label);
      marker
        .getElement()
        ?.addEventListener("focus", () => marker.openTooltip());
      marker
        .getElement()
        ?.addEventListener("blur", () => marker.closeTooltip());
      marker.on("click", () => marker.openTooltip());
    }
    if (bounds.length)
      map.fitBounds(L.latLngBounds(bounds), {
        padding: [50, 50],
        maxZoom: 15,
        animate: false,
      });
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
      layersRef.current = [];
    };
  }, [data, name]);
  // Selection updates existing layers. It never reconstructs the map or resets its view.
  useEffect(() => {
    for (const layer of layersRef.current) {
      const active = layer.ids.includes(activeStopId);
      const highlight = data.crews ? "#ffedb2" : "#ffb45b";
      if (layer.line) {
        layer.line.setStyle({
          color: active ? highlight : layer.color,
          weight: active ? 6 : 4,
        });
        for (const arrow of layer.arrows) {
          const element = arrow.getElement();
          if (element) element.style.color = active ? highlight : layer.color;
        }
      }
      if (layer.marker) {
        layer.marker.getElement()?.classList.toggle("is-active", active);
        layer.buttons?.forEach((button, index) => {
          const selected = layer.ids[index] === activeStopId;
          button.classList.toggle("is-active", selected);
          button.setAttribute("aria-pressed", String(selected));
        });
        if (active)
          layer.marker
            .setTooltipContent(
              textLabel(layer.labels[layer.ids.indexOf(activeStopId)]),
            )
            .openTooltip();
        else layer.marker.closeTooltip();
      }
    }
  }, [activeStopId, data, name]);
  const missingPaths = data.stops
    .filter((stop) => !stop.path.length)
    .map((stop) => `${stop.crewName ? `${stop.crewName} ` : ""}${stop.number}`);
  const missingStops = data.stops
    .filter((stop) => !stop.position)
    .map((stop) => `${stop.crewName ? `${stop.crewName} ` : ""}${stop.number}`);
  return (
    <section className="crew-route-review" aria-label="Crew route review">
      <Typography variant="h5" sx={{ mb: 1 }}>
        {teams ? "Crew routes" : "Planned route"}
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
        {data.crews?.map((crew) => (
          <span
            key={crew.crewId}
            className="crew-map-legend-item"
            data-crew-id={crew.crewId}
          >
            <i
              aria-hidden="true"
              style={{
                display: "inline-block",
                width: 14,
                height: 5,
                backgroundColor: crew.color,
                marginRight: 5,
              }}
            />
            {crew.crewName}
          </span>
        ))}
        {(data.start?.kind === "planned" ||
          data.starts?.some((start) => start.kind === "planned")) && (
          <span>S: planned starting point</span>
        )}
        <span>
          Numbered stops: {teams ? "each crew’s plan order" : "plan order"}
        </span>
        <span>Arrows: supplied path direction</span>
        <span>Dashed: planned path, not recorded movement</span>
        {(data.current || !!data.currentLocations?.length) && (
          <span>C: reported crew location</span>
        )}
      </div>
      {data.crews?.map((crew) => (
        <Typography key={crew.crewId} variant="body2" sx={{ mt: 1 }}>
          {crew.crewName} ·{" "}
          {crew.current
            ? `Reported crew location · GPS: ${gps(crew.current)} · Observed: ${stamp(crew.current.observed_at)} · Source: ${crew.current.source || "Not supplied"}`
            : "Current crew location not supplied."}
          {!crew.start && " Starting-point coordinates not supplied."}
        </Typography>
      ))}
      {!teams && !data.start && (
        <Typography variant="body2" color="text.secondary">
          Starting-point coordinates not supplied.
        </Typography>
      )}
      {!teams && (
        <Typography variant="body2" sx={{ mt: 1 }}>
          {data.current
            ? `Reported crew location · GPS: ${gps(data.current)} · Observed: ${stamp(data.current.observed_at)} · Source: ${data.current.source || "Not supplied"}`
            : "Current crew location not supplied."}
        </Typography>
      )}
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
