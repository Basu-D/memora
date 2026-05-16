import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // In local dev, forward /api/* to the FastAPI backend, stripping /api prefix.
      // This mirrors what nginx does in the Docker stack so behaviour is identical.
      "/api": {
        target:  "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
