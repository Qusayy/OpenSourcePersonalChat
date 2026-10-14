import { execFileSync } from "node:child_process";
import { DB, PYTHON, REPO, SERVER_ENV } from "./playwright.config";

/**
 * Before any test: nobody has a session, and no guesses have been counted.
 * The server may be one left running from the last run, with its database
 * open, so this goes through the gate's own command line and not the file.
 */
export default function globalSetup(): void {
  for (const command of ["revoke", "reset"]) {
    execFileSync(PYTHON, ["-m", "app.gate", command], {
      cwd: REPO,
      env: { ...process.env, ...SERVER_ENV, AURORA_DB_PATH: DB },
      stdio: "pipe",
    });
  }
}
