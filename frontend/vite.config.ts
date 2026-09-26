import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const api = "http://127.0.0.1:8000"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": import.meta.dirname + "/src" } },
  // same origin in the browser: the api (and ./data at /media) sit behind the dev server
  server: { port: 5173, strictPort: true, proxy: { "/api": api, "/media": api } },
})
