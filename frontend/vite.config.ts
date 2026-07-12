import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig } from "vite";

const vitePort = Number(process.env.VITE_PORT ?? 5173);
const apiPort = Number(process.env.VITE_API_PORT ?? 8088);
const apiTarget = `http://localhost:${apiPort}`;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    chunkSizeWarningLimit: 600,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: vitePort,
    proxy: {
      "/api": apiTarget,
      "/reports": apiTarget,
      "/ws": {
        target: `ws://localhost:${apiPort}`,
        ws: true,
      },
    },
  },
});
