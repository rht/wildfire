import { useState } from "react";
import { Button } from "@mui/material";

// The legacy map used CSS perspective, not a terrain/elevation renderer.
export default function MapFrame({ children, mapRef }) {
  const [tilted, setTilted] = useState(false);
  return (
    <div className="map-frame">
      <div className="map-viewport">
        <div className={`map-camera${tilted ? " is-tilted" : ""}`}>
          {children}
        </div>
      </div>
      <div className="map-controls" aria-label="Map view controls">
        <div>
          <Button
            size="small"
            aria-pressed={!tilted}
            onClick={() => setTilted(false)}
          >
            2D
          </Button>
          <Button
            size="small"
            aria-pressed={tilted}
            onClick={() => setTilted(true)}
          >
            3D tilt
          </Button>
        </div>
        <div>
          <Button
            size="small"
            aria-label="Zoom out"
            onClick={() => mapRef.current?.zoomOut()}
          >
            −
          </Button>
          <Button
            size="small"
            aria-label="Zoom in"
            onClick={() => mapRef.current?.zoomIn()}
          >
            +
          </Button>
        </div>
      </div>
      <div className="map-credit">
        <a href="https://www.esri.com/" target="_blank" rel="noreferrer">
          Esri
        </a>{" "}
        World Imagery
      </div>
    </div>
  );
}
