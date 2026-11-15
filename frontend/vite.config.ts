import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  // "ee-assets" rather than Vite's default "assets": Gradio (Hugging Face) serves its own files at /assets
  build: { assetsDir: "ee-assets" },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});
