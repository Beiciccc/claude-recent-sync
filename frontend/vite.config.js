import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, "../src/claude_recent_sync/web_dist"),
    emptyOutDir: true,
    sourcemap: false,
  },
});
