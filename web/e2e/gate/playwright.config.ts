import { pbkdf2Sync } from "node:crypto";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";
import { INSECURE, PYTHON, REPO } from "../../playwright.config";

/* The gate's own browser tests: the real app, armed, with a pattern made up
   for the purpose. The suite beside this one runs with the gate off, so this
   has a server and a database of its own.

     npx playwright test -c e2e/gate/playwright.config.ts

   `npm run e2e` runs both, and so does CI, twice: once as a secure context
   and once under a made-up hostname, which is how the site is really served
   and the run that shows the session cookie works over plain HTTP. */

const PORT = Number(process.env.AURORA_GATE_E2E_PORT ?? 8798);
const HOST = INSECURE ? "aurora.test" : "127.0.0.1";

/** Where the app listens, for requests made from the test process. */
export const APP = `http://127.0.0.1:${PORT}`;
export const DB = fileURLToPath(new URL("../../.e2e/gate.db", import.meta.url));

/** The pattern that opens the site here, as indices into the cover's anchors. */
export const CHAIN = [6, 10, 7, 2, 3, 4, 8];
const SALT = Buffer.from("000102030405060708090a0b0c0d0e0f", "hex");
// The fewest the gate will accept: a test waits on every one.
const ITERATIONS = 100_000;
const digest = pbkdf2Sync(`aurora-gate:v1:${CHAIN.join(",")}`, SALT, ITERATIONS, 32, "sha256");

export const SERVER_ENV = {
  AURORA_MOCK: "true",
  AURORA_DB_PATH: DB,
  AURORA_ALLOW_OUTBOUND: "false",
  AURORA_GATE: "on",
  AURORA_GATE_VERIFIER: `gate1.${ITERATIONS}.${SALT.toString("hex")}.${digest.toString("hex")}`,
  // The insecure run opens the server as aurora.test: one of its names.
  AURORA_ALLOWED_HOSTS: "aurora.test",
};
export { PYTHON, REPO };

export default defineConfig({
  testDir: ".",
  // Not *.spec.ts: the suite beside this one would pick those up and run them
  // against a server with no gate.
  testMatch: "*.gate.ts",
  globalSetup: "./global-setup.ts",
  // Guesses are counted by address, and every test here is one address.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  timeout: 60_000,
  use: {
    baseURL: `http://${HOST}:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chrome",
      use: {
        ...devices["Desktop Chrome"],
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
    // The cover. Every API path answers 401 until the pattern is drawn.
    url: `${APP}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: SERVER_ENV,
  },
});
