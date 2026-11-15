import { fileURLToPath } from "node:url"
import { defineConfig } from "vitest/config"

// Unit tests only: `*.test.ts(x)` next to the code under lib/, components/
// and hooks/. Playwright owns e2e/ (`*.spec.ts`), so neither runner picks
// up the other's files.
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    globals: false,
    include: ["lib/**/*.test.{ts,tsx}", "components/**/*.test.{ts,tsx}", "hooks/**/*.test.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**", ".next/**", "out/**"],
    setupFiles: ["./vitest.setup.ts"],
  },
})
