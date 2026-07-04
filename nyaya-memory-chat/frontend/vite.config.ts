import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// The FastAPI backend runs on :8000. In dev we proxy same-origin so the
// HttpOnly `nyaya_session` cookie is sent automatically (no CORS credentials
// dance). In prod, `npm run build` emits ../web/dist which FastAPI can serve.
const BACKEND = process.env.NYAYA_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: false },
      "/healthz": { target: BACKEND, changeOrigin: false },
      // Clerk OAuth bounces back to /sso-callback — let the SPA handle it in dev.
      "/sso-callback": { target: BACKEND, changeOrigin: false, bypass: () => "/index.html" },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
