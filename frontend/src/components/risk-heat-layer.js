import L from "leaflet";
import { riskHeatColour } from "../state/risk-heat.mjs";

// Screen-space colour halos around assessed locations. No interpolation of fire
// temperature, no density summation, and no estimates for unassessed locations.
export function riskHeatLayer(points) {
  return new (L.Layer.extend({
    onAdd(map) {
      this.map = map;
      this.canvas = L.DomUtil.create("canvas", "risk-heat-canvas");
      this.canvas.setAttribute("aria-hidden", "true");
      map.getPanes().overlayPane.appendChild(this.canvas);
      map.on("moveend zoomend resize", this.draw, this);
      this.draw();
    },
    onRemove(map) {
      map.off("moveend zoomend resize", this.draw, this);
      this.canvas.remove();
    },
    draw() {
      const size = this.map.getSize();
      const ratio = window.devicePixelRatio || 1;
      this.canvas.width = size.x * ratio;
      this.canvas.height = size.y * ratio;
      this.canvas.style.width = `${size.x}px`;
      this.canvas.style.height = `${size.y}px`;
      L.DomUtil.setPosition(
        this.canvas,
        this.map.containerPointToLayerPoint([0, 0]),
      );
      const ctx = this.canvas.getContext("2d");
      ctx.scale(ratio, ratio);
      // Draw lower scores first so overlapping high-risk locations stay visible.
      for (const point of [...points].sort((a, b) => a.score - b.score)) {
        const { x, y } = this.map.latLngToContainerPoint([
          point.latitude,
          point.longitude,
        ]);
        const colour = riskHeatColour(point.score).join(",");
        const gradient = ctx.createRadialGradient(x, y, 0, x, y, 44);
        gradient.addColorStop(0, `rgba(${colour},0.85)`);
        gradient.addColorStop(0.35, `rgba(${colour},0.65)`);
        gradient.addColorStop(1, `rgba(${colour},0)`);
        ctx.fillStyle = gradient;
        ctx.fillRect(x - 44, y - 44, 88, 88);
      }
    },
  }))();
}
