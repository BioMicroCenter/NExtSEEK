import { defineConfig, devices } from "@playwright/test";

const useRealBackend = !!process.env.USE_REAL_BACKEND;

const frontendServer = {
  command: "npm run dev",
  url: "http://localhost:5173",
  reuseExistingServer: !process.env.CI,
};

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "mock",
      use: { ...devices["Desktop Chrome"] },
      testIgnore: "**/real-backend/**",
    },
    ...(useRealBackend
      ? [
          {
            name: "real",
            testDir: "./e2e/real-backend",
            use: { ...devices["Desktop Chrome"] },
          },
        ]
      : []),
  ],
  webServer: frontendServer,
});
