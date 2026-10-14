// @vitest-environment node
import { describe, expect, it } from "vitest";
import { canonical } from "../cover/pattern";
import { ITERATIONS, makeVerifier, strength } from "./verifier";

const CHAIN = [3, 7, 12, 1, 9, 14, 2];
const SALT = Uint8Array.from({ length: 16 }, (_, i) => i);

describe("the line a pattern is kept as", () => {
  // The same figures are asserted in tests/test_gate.py against the server's
  // own hashing. If either side changes how a chain is written down or
  // hashed, one of the two tests says so, before a pattern set here stops
  // opening the site there.
  it("is the one the server makes from the same pattern", async () => {
    expect(canonical(CHAIN)).toBe("aurora-gate:v1:3,7,12,1,9,14,2");
    expect(await makeVerifier(CHAIN, SALT, 600_000)).toBe(
      "gate1.600000.000102030405060708090a0b0c0d0e0f." +
        "4549c411cc40d015ba1a0e1ce608a08f06fae4701cd289de3e2be82bfc7c4f0b",
    );
  });

  it("is letters, digits and dots, which an env file leaves alone", async () => {
    const line = await makeVerifier(CHAIN);
    expect(line).toMatch(/^gate1\.\d+\.[0-9a-f]{32}\.[0-9a-f]{64}$/);
    expect(line.startsWith(`gate1.${ITERATIONS}.`)).toBe(true);
  });

  it("is salted afresh, so the same pattern is never the same line twice", async () => {
    expect(await makeVerifier(CHAIN)).not.toBe(await makeVerifier(CHAIN));
  });

  it("asks the server for no more work than it will do", () => {
    // app/gate.py refuses a verifier outside these.
    expect(ITERATIONS).toBeGreaterThanOrEqual(100_000);
    expect(ITERATIONS).toBeLessThanOrEqual(2_000_000);
  });
});

describe("whether a pattern is worth keeping", () => {
  it("takes one that is long, wide and leaps", () => {
    const found = strength([0, 15, 2, 12, 4, 9, 7]);
    expect(found.enough).toBe(true);
    expect(found.advice).toBe("");
    expect(found.stars).toBe(7);
    expect(found.different).toBe(7);
    expect(found.leaps).toBeGreaterThanOrEqual(2);
  });

  it("refuses one that is too short, and says how short", () => {
    const found = strength([0, 15, 2, 12, 4, 9]);
    expect(found.enough).toBe(false);
    expect(found.advice).toContain("at least 7 stars");
    expect(found.advice).toContain("This has 6.");
  });

  it("refuses one that goes back and forth between a few stars", () => {
    const found = strength([0, 15, 0, 15, 0, 15, 0, 15]);
    expect(found.enough).toBe(false);
    expect(found.different).toBe(2);
    expect(found.advice).toContain("different stars");
  });

  it("refuses one that only ever steps next door", () => {
    // Along the top row of the sky and back down its edge.
    const found = strength([0, 1, 2, 3, 4, 8, 11]);
    expect(found.leaps).toBeLessThan(2);
    expect(found.enough).toBe(false);
    expect(found.advice).toContain("next door");
  });
});
