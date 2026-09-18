import { defineConfig } from "@playwright/test";

// Without a disposable backend (LIBRIS_E2E_URL), only the specs that mock the API can run.
const backend = Boolean(process.env.LIBRIS_E2E_URL);

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60000,
  grepInvert: backend ? undefined : /@integration|@journey/,
  use: {
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  reporter: "list",
  webServer: process.env.CI && !backend ? {
    command: "npx vite preview --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
  } : undefined,
});
