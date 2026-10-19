import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

/* Browser tests run against the real FastAPI app in mock mode, serving the
   built interface from web/dist: build first (`npm run build`).

   Mock mode needs no model and no llama.cpp; it fakes a generator at about
   eleven tokens a second, so everything on the page behaves as it does on the
   server. Outbound requests are switched off: a test must not depend on the
   internet, and the conversations that show fetched data come from
   e2e/seed.py instead.

   Three ways to run, by environment variable:

     (default)              the built files, served by the app
     AURORA_E2E_BASE=url    another origin, e.g. the Vite dev server, where
                            React mounts everything twice on purpose
     AURORA_E2E_INSECURE=1  the same app under a made-up hostname, so the page
                            is not a secure context: no navigator.clipboard,
                            no crypto.randomUUID, as on http://<server ip>  */

const PORT = Number(process.env.AURORA_E2E_PORT ?? 8799);
export const INSECURE = Boolean(process.env.AURORA_E2E_INSECURE);
const HOST = INSECURE ? "aurora.test" : "127.0.0.1";

/** Where the app itself listens, whatever name the browser is given for it.
 *  Requests made from the test process (not from the page) go here: Node does
 *  not know the made-up hostname, and the dev server is not the app. */
export const APP = `http://127.0.0.1:${PORT}`;
/** True when the page under test is not served by the app (the dev server). */
export const FOREIGN_PAGE = Boolean(process.env.AURORA_E2E_BASE);

export const REPO = fileURLToPath(new URL("..", import.meta.url));
// Not under test-results: Playwright empties that folder as a run starts,
// and the server already has this file open by then.
export const DB = fileURLToPath(new URL("./.e2e/aurora.db", import.meta.url));
export const PYTHON =
  process.env.AURORA_PYTHON ??
  (process.platform === "win32" ? ".venv\\Scripts\\python.exe" : "python");
export const SERVER_ENV = {
  AURORA_MOCK: "true",
  AURORA_DB_PATH: DB,
  AURORA_ALLOW_OUTBOUND: "false",
  // These specs are about the application, so the gate in front of it is
  // open. The gate has a suite of its own: e2e/gate.
  AURORA_GATE: "off",
  // The server answers only to its own names and these. The insecure run
  // opens it as aurora.test, which would otherwise be refused (421).
  AURORA_ALLOWED_HOSTS: "aurora.test",
};

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  // One engine behind one lock: two tests that send would queue behind each
  // other and each would see the other's wait.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  timeout: 60_000,
  use: {
    baseURL: process.env.AURORA_E2E_BASE ?? `http://${HOST}:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chrome",
      use: {
        ...devices["Desktop Chrome"],
        // The Chrome already on a development machine; CI installs Chromium.
        channel: process.env.CI ? undefined : "chrome",
        launchOptions: {
          args: INSECURE ? ["--host-resolver-rules=MAP aurora.test 127.0.0.1"] : [],
        },
      },
    },
  ],
  webServer: {
    command: `${PYTHON} -m uvicorn app.main:app --host 127.0.0.1 --port ${PORT} --log-level warning`,
    cwd: REPO,
    url: `${APP}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: SERVER_ENV,
  },
});
