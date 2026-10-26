import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      // Same-origin API in development; the backend is never exposed to arbitrary origins.
      "/api": { target: apiTarget, changeOrigin: false },
    },
  },
  preview: { host: "127.0.0.1", port: 4173, proxy: { "/api": { target: apiTarget } } },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
