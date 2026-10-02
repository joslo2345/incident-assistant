import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In production nginx serves the build and proxies /api to the agent API (same origin, so the
// session cookie stays SameSite=Strict). `npm run dev` does the same with Vite's proxy.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Charts (recharts) make one ~600 kB chunk; fine for an internal tool on a LAN/VPN.
  build: { chunkSizeWarningLimit: 800 },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8002",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
