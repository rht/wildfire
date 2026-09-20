import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
export default defineConfig({
  plugins: [
    react(),
    {
      name: "mantis-attribution",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "assets/mantis-license.txt",
          source: readFileSync(
            new URL("./vendor/mantis/LICENSE", import.meta.url),
            "utf8",
          ),
        });
      },
    },
  ],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": { target: "http://127.0.0.1:18522", ws: true } },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: "map", test: /node_modules\/leaflet/ },
            {
              name: "react",
              test: /node_modules\/(react|react-dom|scheduler)\//,
            },
          ],
        },
      },
    },
  },
});
