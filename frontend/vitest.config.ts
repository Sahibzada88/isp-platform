import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  esbuild: { jsx: "automatic" },
  test: { environment: "node", include: ["src/**/*.test.ts", "src/**/*.test.tsx"], setupFiles: ["./src/test-setup.ts"] },
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
});
