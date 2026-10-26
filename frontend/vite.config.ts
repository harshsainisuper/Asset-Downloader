import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

// On Vercel the UI is static and the API runs elsewhere: fail the build early if the
// backend URL is missing or not HTTPS (browsers block HTTP calls from an HTTPS page).
if (process.env.VERCEL) {
  const base = process.env.VITE_API_BASE_URL ?? "";
  if (!/^https:\/\/[^/\s]+/.test(base)) {
    throw new Error(
      "VITE_API_BASE_URL must be set to your backend's https:// URL in the Vercel project's " +
        "Environment Variables (e.g. https://yt-media-toolkit-api.onrender.com). See README → Deploying.",
    );
  }
}

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
