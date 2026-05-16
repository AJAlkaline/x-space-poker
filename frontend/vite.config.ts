import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the frontend runs on :5173 and the backend on :8000. We proxy
// the API and WebSocket paths through Vite so the frontend can use
// same-origin URLs. In production, the FastAPI app serves the built
// frontend (see backend/app/api/main.py); these proxies have no effect
// there.
//
// All paths pass through unchanged — backend routes match frontend paths
// exactly (/api/tables/*, /auth/*, /ws/*).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/auth": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true, changeOrigin: true },
    },
  },
});
