import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// CLIPPER_API: another api than the local stack's, e.g. a review copy on another port
const api = process.env.CLIPPER_API || "http://127.0.0.1:8000"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": import.meta.dirname + "/src" } },
  // same origin in the browser: the api (and ./data at /media) sit behind the dev server
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": api, "/media": api },
    // ponytail: demo-only tunnel host, no auth in front of it — remove once the demo is over
    allowedHosts: ["discount-employment-disabled-minerals.trycloudflare.com"],
  },
})
