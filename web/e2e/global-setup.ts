import { reseed } from "./support";

/** Before any test: the database holds exactly the seeded conversations. */
export default function globalSetup(): void {
  reseed();
}
