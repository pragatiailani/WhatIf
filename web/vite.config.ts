import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 127.0.0.1, not "localhost": Node resolves localhost to ::1 only, so http://127.0.0.1:5173 would
  // not open. Browsers fall back from localhost to 127.0.0.1, so both addresses work.
  server: { host: "127.0.0.1", port: 5173 },
});
