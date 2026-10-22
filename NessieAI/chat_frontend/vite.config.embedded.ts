import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: path.resolve(__dirname, "../../static/js/chat_assistant"),
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: path.resolve(__dirname, "src/main.embedded.tsx"),
    },
  },
  base: "/static/js/chat_assistant/",
  // Pin the project root to this package so the build, its env files and the
  // outDir above resolve the same way whatever directory vite is launched from.
  // Kept below `base` so the line numbers the unit docs cite stay valid.
  root: __dirname,
  // The embedded page logs in with the session cookie: never inline a login.
  define: {
    "import.meta.env.VITE_API_USER": '""',
    "import.meta.env.VITE_API_PASS": '""',
  },
});
