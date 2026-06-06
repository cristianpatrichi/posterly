import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev (`npm run dev` on :5173) proxy /api to the FastAPI backend on :8787.
// In the Docker prod build the SPA is served by FastAPI itself, so the same
// relative `/api/...` URLs resolve same-origin and no proxy is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8787",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
