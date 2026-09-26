// Runs only the gallery contract writer (npm run gallery:contract); the normal test run
// never includes scripts/.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", include: ["scripts/**/*.write.test.ts"] },
});
