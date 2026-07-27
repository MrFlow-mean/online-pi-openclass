import { defineConfig, devices } from "@playwright/test";
import os from "node:os";
import path from "node:path";

const rootDir = path.resolve(__dirname, "../..");
const apiPort = Number(process.env.OPENCLASS_E2E_API_PORT ?? 8110);
const webPort = Number(process.env.OPENCLASS_E2E_WEB_PORT ?? 3110);
const apiBaseUrl = `http://127.0.0.1:${apiPort}`;
const webBaseUrl = `http://127.0.0.1:${webPort}`;
const e2eDataDir = path.join(os.tmpdir(), "openclass-e2e");
const pythonExecutable = process.env.OPENCLASS_E2E_PYTHON ?? ".venv/bin/python";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: webBaseUrl,
    trace: "on-first-retry",
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: `${pythonExecutable} -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port ${apiPort}`,
      cwd: rootDir,
      url: `${apiBaseUrl}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        ...process.env,
        OPENCLASS_DATABASE_PATH: path.join(e2eDataDir, "openclass.sqlite3"),
        OPENCLASS_UPLOAD_DIR: path.join(e2eDataDir, "uploads"),
        OPENCLASS_EXPORT_DIR: path.join(e2eDataDir, "exports"),
        OPENCLASS_PUBLIC_ORIGIN: webBaseUrl,
        OPENCLASS_CLOUDFLARE_TURNSTILE_ENABLED: "false",
      },
    },
    {
      command: `npm --prefix apps/web run build && npm --prefix apps/web run start -- --hostname 127.0.0.1 --port ${webPort}`,
      cwd: rootDir,
      url: webBaseUrl,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        ...process.env,
        NEXT_PUBLIC_API_BASE_URL: apiBaseUrl,
        NEXT_PUBLIC_OPENCLASS_E2E_MODE: "true",
        OPENCLASS_NEXT_DIST_DIR: ".next-e2e",
      },
    },
  ],
});
