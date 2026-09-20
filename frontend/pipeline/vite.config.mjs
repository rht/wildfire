import { fileURLToPath } from "node:url";
import { realpathSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    fs: {
      // Shared dependencies may be symlinked from a sibling worktree.
      allow: [
        fileURLToPath(new URL("..", import.meta.url)),
        realpathSync(new URL("../node_modules", import.meta.url)),
      ],
    },
  },
  build: { outDir: "../dist/pipeline", emptyOutDir: true },
});
