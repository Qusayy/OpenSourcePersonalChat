import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The Python app answers /api. In development Vite serves the page and passes
// those requests through, the streamed ones included.
const api = process.env.AURORA_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: Number(process.env.AURORA_WEB_PORT ?? 5173),
    strictPort: true,
    proxy: { "/api": { target: api } },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Two pages. The application, and the cover that a browser with no
    // session is sent in its place (app/gate.py). They share no code: what
    // the cover's page loads is all a visitor ever fetches.
    rolldownOptions: {
      input: {
        index: fileURLToPath(new URL("./index.html", import.meta.url)),
        cover: fileURLToPath(new URL("./cover.html", import.meta.url)),
      },
    },
  },
  test: {
    environment: "happy-dom",
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
