import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const localHost = "127.0.0.1";

export default defineConfig({
  plugins: [react()],
  server: {
    host: localHost,
    port: 5173,
    strictPort: true,
  },
  preview: {
    host: localHost,
    port: 4173,
    strictPort: true,
  },
  test: {
    allowOnly: false,
    clearMocks: true,
    environment: "jsdom",
    globals: false,
    include: ["src/**/*.test.{ts,tsx}"],
    passWithNoTests: false,
    restoreMocks: true,
    setupFiles: ["./src/test/setup.ts"],
    unstubEnvs: true,
    unstubGlobals: true,
  },
});
